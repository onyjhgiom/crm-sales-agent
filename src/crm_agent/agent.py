"""对外统一入口：一行 `ask()` 拿到回答、证据和执行轨迹。

CLI、FastAPI、评估脚本都走这里，保证三条路径的行为完全一致——
评估出来的分数才和真实使用对得上。

生产侧补充：
- 每次请求后按 FIFO 淘汰会话，避免 checkpointer 内存无界增长；
- 内部异常只在服务端记日志，返回给客户端的是通用文案，不泄露路径/堆栈；
- 暴露 requests / errors / 平均耗时 / 会话数，供 /api/health 做探活与观测。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

from .context import Context, build_context
from .graph import build_graph, prune_sessions

log = logging.getLogger("crm_agent.agent")


class SalesAgent:
    def __init__(self, ctx: Context | None = None, use_llm: bool = True):
        self.ctx = ctx or build_context(use_llm=use_llm)
        self.graph = build_graph(self.ctx, with_memory=self.ctx.settings.use_checkpointer)
        self._has_memory = getattr(self.graph, "checkpointer", None) is not None
        self._lock = threading.Lock()
        self._prune_lock = threading.Lock()
        self.requests = 0
        self.errors = 0
        self.total_latency_ms = 0.0
        self.started_at = time.time()

    # ------------------------------------------------------------------
    def ask(self, question: str, thread_id: str | None = None,
            advertiser_id: str | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        thread_id = thread_id or str(uuid.uuid4())
        init: dict[str, Any] = {
            "question": question,
            "advertiser_id": advertiser_id,   # 显式置空，避免上一轮的客户被 checkpointer 带过来
            "trace": [],
            "retry": 0,
        }
        with self._lock:
            self.requests += 1

        try:
            if self._has_memory:
                state = self.graph.invoke(init, config={"configurable": {"thread_id": thread_id}})
            else:
                state = self.graph.invoke(init)
            result = {
                "answer": state.get("answer", ""),
                "intent": state.get("intent"),
                "advertiser_id": state.get("advertiser_id"),
                "trace": state.get("trace", []),
                "evidence": state.get("evidence", {}),
                "citations": state.get("citations", []),
                "review": state.get("review", {}),
            }
        except Exception as exc:  # noqa: BLE001 - 单次失败不应让服务 500
            log.exception("agent 执行失败，已降级为通用错误响应")
            with self._lock:
                self.errors += 1
            result = {
                "answer": "服务暂时不可用，请稍后重试。",
                "intent": "error",
                "trace": [{"step": "error", "detail": "internal_error"}],
                "evidence": {},
                "citations": [],
                "review": {"ok": False, "issues": ["internal_error"]},
            }
        finally:
            prune_sessions(self.graph, self.ctx.settings.max_sessions, self._prune_lock)
            with self._lock:
                self.total_latency_ms += (time.perf_counter() - started) * 1000

        return result

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            cp = getattr(self.graph, "checkpointer", None)
            sessions = len(getattr(cp, "storage", {})) if cp is not None else 0
            return {
                "requests": self.requests,
                "errors": self.errors,
                "avg_latency_ms": round(self.total_latency_ms / self.requests, 2) if self.requests else 0.0,
                "sessions": sessions,
                "max_sessions": self.ctx.settings.max_sessions,
                "uptime_s": round(time.time() - self.started_at, 1),
            }

    def close(self) -> None:
        self.ctx.store.close()
