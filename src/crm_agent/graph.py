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

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .context import Context
from .nodes import analysis, knowledge, opportunity, profile, review, router, smalltalk
from .state import AgentState

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
    g.add_node("profile", lambda s: profile.profile_node(s, ctx))
    g.add_node("opportunity", lambda s: opportunity.opportunity_node(s, ctx))
    g.add_node("analysis", lambda s: analysis.analysis_node(s, ctx))
    g.add_node("knowledge", lambda s: knowledge.knowledge_node(s, ctx))
    g.add_node("smalltalk", lambda s: smalltalk.smalltalk_node(s, ctx))
    g.add_node("review", lambda s: review.review_node(s, ctx))

    g.add_edge(START, "router")
    g.add_conditional_edges("router", lambda s: s["intent"], _INTENT_NODES)

    for node in _INTENT_NODES.values():
        g.add_edge(node, "review")

    def _after_review(state: AgentState) -> str:
        rv = state.get("review", {})
        if rv.get("ok", True) or rv.get("retried"):
            return END
        return state.get("intent", "smalltalk")

    g.add_conditional_edges("review", _after_review, {**_INTENT_NODES, END: END})

    return g.compile(checkpointer=MemorySaver() if with_memory else None)
