"""LangGraph 图装配。

START → router ─┬─> profile ─┐
                ├─> opportunity ─┤
                ├─> analysis ────┼─> review ─┬─(未通过且未重试)─> 回到原子 Agent
                ├─> knowledge ───┤            └─(通过或已重试)──> END
                └─> smalltalk ───┘

为什么用"单图 + 条件边"而不是每个能力各起一个 Agent 互相调用：
销售一句话里往往只有一个意图，串行编排反而增加延迟；把路由放在最前面，
后面每个节点只做一件事，评估时也能按意图分别统计准确率。
"""

from __future__ import annotations

import logging

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .context import Context
from .nodes import analysis, knowledge, opportunity, orchestrate, profile, review, router, smalltalk
from .state import AgentState

log = logging.getLogger("crm_agent.graph")

_INTENT_NODES = {
    "profile": "profile",
    "opportunity": "opportunity",
    "analysis": "analysis",
    "knowledge": "knowledge",
    "smalltalk": "smalltalk",
}


def build_graph(ctx: Context, with_memory: bool = True):
    g = StateGraph(AgentState)

    g.add_node("router", lambda s: router.route(s, ctx))
    g.add_node("orchestrate", lambda s: orchestrate.orchestrate_node(s, ctx))
    g.add_node("profile", lambda s: profile.profile_node(s, ctx))
    g.add_node("opportunity", lambda s: opportunity.opportunity_node(s, ctx))
    g.add_node("analysis", lambda s: analysis.analysis_node(s, ctx))
    g.add_node("knowledge", lambda s: knowledge.knowledge_node(s, ctx))
    g.add_node("smalltalk", lambda s: smalltalk.smalltalk_node(s, ctx))
    g.add_node("review", lambda s: review.review_node(s, ctx))

    g.add_edge(START, "router")

    def _dispatch(state: AgentState) -> str:
        # 只有规划器明确标记为 composite 才走编排；单意图路径与旧版逐字节一致。
        plan = state.get("plan") or {}
        if plan.get("mode") == "composite" and len(plan.get("tasks", [])) > 1:
            return "orchestrate"
        return state.get("intent", "smalltalk")

    g.add_conditional_edges("router", _dispatch, {**_INTENT_NODES, "orchestrate": "orchestrate"})

    for node in (*_INTENT_NODES.values(), "orchestrate"):
        g.add_edge(node, "review")

    def _after_review(state: AgentState) -> str:
        rv = state.get("review", {})
        if rv.get("ok", True) or rv.get("retried"):
            return END
        plan = state.get("plan") or {}
        if plan.get("mode") == "composite":
            return "orchestrate"
        return state.get("intent", "smalltalk")

    g.add_conditional_edges("review", _after_review, {**_INTENT_NODES, END: END})

    return g.compile(checkpointer=MemorySaver() if with_memory else None)


def prune_sessions(graph, max_sessions: int, lock) -> int:
    """按 FIFO 淘汰最旧会话，给内存中的 checkpointer 设上界。

    MemorySaver 会为每个新 thread_id 永久保存 checkpoint/writes/blobs；公网服务里
    每个访客都是一个新会话，不淘汰就是一条线性内存泄漏（实测 +500 会话 ≈ +12 MB）。
    这里在每次请求后把会话数压回 max_sessions；清理失败不影响本次回答。
    """
    cp = getattr(graph, "checkpointer", None)
    if cp is None or max_sessions <= 0:
        return 0
    try:
        with lock:
            storage = getattr(cp, "storage", None)
            if storage is None:
                return 0
            # 滞回 + 批量淘汰：超过上限 10% 才动手，一次清到 80%。
            # 否则每来一个请求都要扫一遍 writes/blobs（数千个 key），并发下会拖出秒级长尾。
            high = max(max_sessions + 1, int(max_sessions * 1.1))
            if len(storage) <= high:
                return 0
            target = max(1, int(max_sessions * 0.8))
            doomed = list(storage)[: max(1, len(storage) - target)]
            doomed_set = set(doomed)
            for thread_id in doomed:
                storage.pop(thread_id, None)
            for container in (getattr(cp, "writes", None), getattr(cp, "blobs", None)):
                if not isinstance(container, dict):
                    continue
                stale = [k for k in list(container)
                         if isinstance(k, tuple) and k and k[0] in doomed_set]
                for key in stale:
                    container.pop(key, None)
            return len(doomed)
    except Exception as exc:  # noqa: BLE001 - 淘汰是尽力而为，绝不能影响主链路
        log.debug("session prune skipped: %s", type(exc).__name__)
        return 0
