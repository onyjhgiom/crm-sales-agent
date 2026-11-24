"""对外统一入口：一行 `ask()` 拿到回答、证据和执行轨迹。

CLI、FastAPI、评估脚本都走这里，保证三条路径的行为完全一致——
评估出来的分数才和真实使用对得上。

生产侧补充：
- 每次请求后按 FIFO 淘汰会话，避免 checkpointer 内存无界增长；
- 内部异常只在服务端记日志，返回给客户端的是通用文案，不泄露路径/堆栈；
- 暴露 requests / errors / 平均耗时 / 会话数，供 /api/health 做探活与观测。

上下文记忆：
- 调用图之前从 ConversationMemory 读出该 session 的历史，放进 state.history；
- 路由据此做指代消解（"他/这家/继续"），模型在线时还能看到对话摘要；
- 调用成功后把这一轮问答写回记忆。记忆层与 checkpointer 相互独立：
  生产模式关掉 checkpointer 后，多轮上下文依然有效。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

from .context import Context, build_context
from .errors import INTERNAL_ERROR, AgentError
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
        history = self._history(thread_id)
        init: dict[str, Any] = {
            "question": question,
            "advertiser_id": advertiser_id,   # 显式置空，避免上一轮的客户被 checkpointer 带过来
            "history": history,               # 对话上下文（记忆层），用于指代消解与模型背景
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
                # 新增的结构化字段：旧调用方只取前几项，不受影响
                "plan": state.get("plan"),
                "structure": state.get("structure"),
                "confidence": state.get("confidence"),
                "degraded": bool(state.get("degraded", False)),
                "warnings": state.get("warnings", []),
                "tool_results": state.get("tool_results", []),
            }
        except Exception as exc:  # noqa: BLE001 - 单次失败不应让服务 500
            log.exception("agent 执行失败，已降级为通用错误响应")
            with self._lock:
                self.errors += 1
            err = AgentError(INTERNAL_ERROR, detail=type(exc).__name__, stage="graph")
            result = {
                "answer": err.message,
                "intent": "error",
                "trace": [{"step": "error", "detail": err.code}],
                "evidence": {"error": err.to_dict()},
                "citations": [],
                "review": {"ok": False, "issues": [err.code]},
                "error": err.to_dict(),
                "plan": None, "structure": None,
                "confidence": 0.0, "degraded": True,
                "warnings": [err.message], "tool_results": [],
            }
        finally:
            prune_sessions(self.graph, self.ctx.settings.max_sessions, self._prune_lock)
            with self._lock:
                self.total_latency_ms += (time.perf_counter() - started) * 1000

        # 成功回答才记入上下文；失败的降级响应不污染记忆
        memory = self.ctx.memory
        if memory is not None and result.get("intent") != "error":
            count = memory.append_turn(
                thread_id, question, result["answer"],
                result.get("intent"), result.get("advertiser_id"),
            )
            result["turn"] = count
            result["history_turns"] = memory.stats()
        else:
            result["turn"] = len(history)
            result["history_turns"] = memory.stats() if memory else {"enabled": False}
        return result

    # ------------------------------------------------------------------
    def _history(self, session_id: str) -> list[dict[str, Any]]:
        memory = self.ctx.memory
        if memory is None:
            return []
        return [t.to_dict() for t in memory.get(session_id)]

    def history(self, session_id: str) -> list[dict[str, Any]]:
        """给 API 用：返回该会话的完整轮次。"""
        return self._history(session_id)

    def clear_history(self, session_id: str) -> bool:
        memory = self.ctx.memory
        return memory.clear(session_id) if memory else False

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
                "memory": self.ctx.memory.stats() if self.ctx.memory else {"enabled": False},
                "tools": self.ctx.registry.metrics() if self.ctx.registry else {"enabled": False},
                "planning": {
                    "enabled": bool(self.ctx.planner and self.ctx.planner.enabled),
                    "max_steps": self.ctx.planner.max_steps if self.ctx.planner else 0,
                },
            }

    def close(self) -> None:
        if self.ctx.memory:
            self.ctx.memory.close()
        self.ctx.store.close()
