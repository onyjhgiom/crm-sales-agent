"""FastAPI 服务：把 Agent 暴露成一个网页可用的接口。

启动：
    python -m uvicorn api.server:app --port 8000
    HOST=0.0.0.0 PORT=8000 python api/server.py     # 容器/平台部署

接口：
    GET  /api/health      健康检查（模式、数据、模型熔断状态、运行时指标）
    GET  /api/metrics     运行时指标
    GET  /api/advertisers 客户列表（前端下拉/侧栏用）
    POST /api/chat        提问，返回回答 + 证据 + 执行轨迹 + 上下文轮次
    GET  /api/history     查看会话记忆（?session_id=xxx）
    DELETE /api/history   清空会话记忆（?session_id=xxx），开始新话题

生产稳定性：
- lifespan 统一构建/释放 Agent，启动即建索引，关闭时释放 SQLite 连接；
- 单请求超时、单 IP 限流、请求体长度校验，避免慢请求或滥用拖垮线程池；
- 全局异常处理只返回通用错误，堆栈只落在服务端日志；
- 每次请求带 request_id 与耗时日志，便于定位但不记录 API Key。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from crm_agent.agent import SalesAgent  # noqa: E402
from crm_agent.config import load_settings  # noqa: E402

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("crm_agent.api")

settings = load_settings()
VERSION = "0.2.0"


class RateLimiter:
    """单 IP 滑动窗口限流，纯内存、无外部依赖。"""

    def __init__(self, rpm: int):
        self.rpm = rpm
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > 2000:  # 顺手清理过期 IP，避免限流表本身膨胀
                for k in [k for k, q in list(self._hits.items()) if not q or now - q[-1] > 60]:
                    self._hits.pop(k, None)
            q = self._hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.rpm:
                return False
            q.append(now)
            return True


limiter = RateLimiter(settings.rate_limit_rpm)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.agent = SalesAgent()
    log.info("agent ready: mode=%s data=%s", app.state.agent.ctx.settings.mode,
             app.state.agent.ctx.settings.db_path)
    try:
        yield
    finally:
        app.state.agent.close()
        log.info("agent closed")


app = FastAPI(title="CRM 销售提效 Agent", version=VERSION, lifespan=lifespan)


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=settings.max_question_chars)
    session_id: str | None = None
    advertiser_id: str | None = None


@app.middleware("http")
async def request_guard(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    request.state.request_id = rid
    started = time.perf_counter()

    if request.url.path == "/api/chat" and request.method == "POST":
        ip = request.client.host if request.client else "unknown"
        if not limiter.allow(ip):
            return JSONResponse({"error": "rate_limited", "request_id": rid},
                                status_code=429, headers={"Retry-After": "60"})

    try:
        response = await call_next(request)
    except Exception:  # noqa: BLE001 - 不把内部堆栈回显给客户端
        log.exception("unhandled error rid=%s path=%s", rid, request.url.path)
        return JSONResponse({"error": "internal_error", "request_id": rid}, status_code=500)

    response.headers["X-Request-ID"] = rid
    log.info("rid=%s path=%s status=%s cost=%.1fms", rid, request.url.path,
             response.status_code, (time.perf_counter() - started) * 1000)
    return response


@app.get("/api/health")
def health(request: Request) -> dict:
    agent: SalesAgent = request.app.state.agent
    ctx = agent.ctx
    return {
        "status": "ok",
        "version": VERSION,
        "mode": ctx.settings.mode,
        "model": ctx.settings.model if ctx.llm.available else "offline-rules",
        "advertisers": ctx.store.query("SELECT COUNT(*) AS n FROM advertisers")[0]["n"],
        "kb": ctx.kb.stats(),
        "llm": ctx.llm.metrics(),
        "runtime": agent.metrics(),
        "limits": {
            "llm_timeout_s": ctx.settings.llm_timeout,
            "request_timeout_s": ctx.settings.request_timeout,
            "rate_limit_rpm": settings.rate_limit_rpm,
            "max_sessions": ctx.settings.max_sessions,
            "tool_timeout_s": ctx.settings.tool_timeout_s,
            "tool_max_retries": ctx.settings.tool_max_retries,
            "planning_enabled": bool(ctx.planner and ctx.planner.enabled),
        },
    }


@app.get("/api/tools")
def tools(request: Request) -> dict:
    """工具目录 + 调用指标：接入新工具后无需改前端即可发现。"""
    ctx = request.app.state.agent.ctx
    registry = ctx.registry
    return {
        "tools": registry.describe() if registry else [],
        "metrics": registry.metrics() if registry else {"enabled": False},
    }


@app.get("/api/metrics")
def metrics(request: Request) -> dict:
    agent: SalesAgent = request.app.state.agent
    return {"runtime": agent.metrics(), "llm": agent.ctx.llm.metrics()}


@app.get("/api/advertisers")
def advertisers(request: Request) -> list[dict]:
    return request.app.state.agent.ctx.store.list_advertisers(limit=50)


@app.get("/api/history")
def get_history(request: Request, session_id: str) -> dict:
    agent: SalesAgent = request.app.state.agent
    return {
        "session_id": session_id,
        "turns": agent.history(session_id),
        "stats": agent.ctx.memory.stats() if agent.ctx.memory else {"enabled": False},
    }


@app.delete("/api/history")
def clear_history(request: Request, session_id: str) -> dict:
    agent: SalesAgent = request.app.state.agent
    return {"session_id": session_id, "cleared": agent.clear_history(session_id)}


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request) -> JSONResponse:
    agent: SalesAgent = request.app.state.agent
    rid = request.state.request_id
    # to_thread 启动的工作线程无法被 wait_for 中断：超时后用它通知 agent 跳过记忆写入，
    # 避免客户端收到 504 后会话里却多了一条从未展示过的回答。
    cancelled = threading.Event()
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(agent.ask, req.question, req.session_id, req.advertiser_id, cancelled),
            timeout=settings.request_timeout,
        )
    except asyncio.TimeoutError:
        cancelled.set()
        log.warning("request timeout rid=%s timeout=%.1fs", rid, settings.request_timeout)
        return JSONResponse({"error": "request_timeout", "request_id": rid}, status_code=504)

    if result.get("intent") == "error":
        log.error("agent degraded rid=%s", rid)
    return JSONResponse(result)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(ROOT, "web", "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.server:app", host=os.getenv("HOST", "127.0.0.1"),
                port=int(os.getenv("PORT", "8000")), reload=False)
