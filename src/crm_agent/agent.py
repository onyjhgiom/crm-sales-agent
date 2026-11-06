"""对外统一入口：一行 `answer()` 拿到回答、证据和执行轨迹。

CLI、FastAPI、评估脚本都走这里，保证三条路径的行为完全一致——
评估出来的分数才和真实使用对得上。
"""

from __future__ import annotations

import uuid
from typing import Any

from .context import Context, build_context
from .graph import build_graph


class SalesAgent:
    def __init__(self, ctx: Context | None = None, use_llm: bool = True):
        self.ctx = ctx or build_context(use_llm=use_llm)
        self.graph = build_graph(self.ctx)

    # ------------------------------------------------------------------
    def ask(self, question: str, thread_id: str | None = None,
            advertiser_id: str | None = None) -> dict[str, Any]:
        thread_id = thread_id or str(uuid.uuid4())
        init: dict[str, Any] = {
            "question": question,
            "advertiser_id": advertiser_id,   # 显式置空，避免上一轮的客户被 checkpointer 带过来
            "trace": [],
            "retry": 0,
        }

        try:
            state = self.graph.invoke(init, config={"configurable": {"thread_id": thread_id}})
        except Exception as exc:  # noqa: BLE001 - 单次失败不应让服务 500
            return {
                "answer": f"执行出错：{type(exc).__name__}: {exc}",
                "intent": "error",
                "trace": [{"step": "error", "detail": str(exc)}],
                "evidence": {},
                "citations": [],
                "review": {"ok": False, "issues": [str(exc)]},
            }

        return {
            "answer": state.get("answer", ""),
            "intent": state.get("intent"),
            "advertiser_id": state.get("advertiser_id"),
            "trace": state.get("trace", []),
            "evidence": state.get("evidence", {}),
            "citations": state.get("citations", []),
            "review": state.get("review", {}),
        }

    def close(self) -> None:
        self.ctx.store.close()
