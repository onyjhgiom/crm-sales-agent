"""FastAPI 服务：把 Agent 暴露成一个网页可用的接口。

启动：
    python -m uvicorn api.server:app --port 8000
或：
    python api/server.py

三个接口：
    GET  /api/health      健康检查（返回当前模式与数据规模）
    GET  /api/advertisers 客户列表（前端下拉/侧栏用）
    POST /api/chat        提问，返回回答 + 证据 + 执行轨迹
"""

from __future__ import annotations

import os
import sys

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from crm_agent.agent import SalesAgent  # noqa: E402

app = FastAPI(title="CRM 销售提效 Agent", version="0.1.0")
agent = SalesAgent()


class ChatRequest(BaseModel):
    question: str
    session_id: str | None = None
    advertiser_id: str | None = None


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "mode": agent.ctx.settings.mode,
        "model": agent.ctx.settings.model if agent.ctx.llm.available else "offline-rules",
        "advertisers": agent.ctx.store.query("SELECT COUNT(*) AS n FROM advertisers")[0]["n"],
        "kb": agent.ctx.kb.stats(),
    }


@app.get("/api/advertisers")
def advertisers() -> list[dict]:
    return agent.ctx.store.list_advertisers(limit=50)


@app.post("/api/chat")
def chat(req: ChatRequest) -> JSONResponse:
    if not req.question.strip():
        return JSONResponse({"error": "问题不能为空"}, status_code=400)
    result = agent.ask(req.question, thread_id=req.session_id, advertiser_id=req.advertiser_id)
    return JSONResponse(result)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(ROOT, "web", "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.server:app", host="127.0.0.1", port=8000, reload=False)
