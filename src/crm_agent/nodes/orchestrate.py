"""复合任务编排节点。

只处理 Planner 标记为 composite 的问题（单意图完全不经过这里）：
1. 按 plan.tasks 逐个调用对应的原子节点，复用它们原有的渲染逻辑；
2. 同时把计划里的工具步骤交给 ToolRegistry 做统一编排（依赖引用、重试、回退）；
3. 子任务失败只影响该项，其他项照常出结果；
4. 最后由 integration.integrate() 合成结构化回答与 Markdown 正文。

这样"更智能"不会牺牲稳定性：即便某个工具或某个子任务失败，用户仍拿到部分可用结果。
"""

from __future__ import annotations

import logging
from typing import Any

from ..errors import INTERNAL_ERROR, AgentError
from ..integration import integrate
from ..state import AgentState
from . import analysis, knowledge, opportunity, profile, smalltalk

log = logging.getLogger("crm_agent.orchestrate")

_NODES = {
    "profile": profile.profile_node,
    "opportunity": opportunity.opportunity_node,
    "analysis": analysis.analysis_node,
    "knowledge": knowledge.knowledge_node,
    "smalltalk": smalltalk.smalltalk_node,
}


def _tool_steps(tasks: list[dict[str, Any]], ctx: Any = None) -> list[dict[str, Any]]:
    """把每个子任务的计划工具翻译成可编排的步骤（带 $ref 依赖）。"""
    steps: list[dict[str, Any]] = []
    top_k = getattr(getattr(ctx, "settings", None), "top_k", 4)
    for task in tasks:
        tid = str(task.get("id", "t"))
        tools = set(task.get("tools") or [])
        adv = task.get("advertiser_id")
        question = task.get("question", "")
        if adv and "advertiser.get" in tools:
            steps.append({"id": f"{tid}_adv", "tool": "advertiser.get",
                          "params": {"advertiser_id": adv}})
        if "kb.retrieve" in tools:
            steps.append({"id": f"{tid}_kb", "tool": "kb.retrieve",
                          "params": {"query": question, "top_k": top_k}})
        if "sql.generate" in tools:
            # 没有显式客户时不要传 None：参数 schema 要求 str，会误判为校验失败
            # 并连带让下游 sql.execute 因依赖失败而跳过。
            sql_params: dict[str, Any] = {"question": question}
            if adv:
                sql_params["advertiser_id"] = adv
            steps.append({"id": f"{tid}_sql", "tool": "sql.generate",
                          "params": sql_params})
            steps.append({"id": f"{tid}_run", "tool": "sql.execute",
                          "params": {"sql": {"$ref": f"{tid}_sql.0"}},
                          "depends_on": [f"{tid}_sql"], "optional": True})
        if "metrics.list" in tools and adv:
            steps.append({"id": f"{tid}_metrics", "tool": "metrics.list",
                          "params": {"advertiser_id": adv}, "optional": True})
    return steps


def orchestrate_node(state: AgentState, ctx: Any) -> dict[str, Any]:
    plan = state.get("plan") or {}
    tasks = plan.get("tasks") or []
    trace = list(state.get("trace", []))
    trace.append({"step": "orchestrate", "detail": f"开始执行 {len(tasks)} 个子任务"})

    # 工具层编排：失败不中断，结果供置信度与证据汇总使用
    registry = getattr(ctx, "registry", None)
    tool_results: list[Any] = []
    if registry is not None and tasks:
        try:
            tool_results = registry.call_plan(_tool_steps(tasks, ctx), ctx=ctx)
        except Exception as exc:  # noqa: BLE001 - 编排层异常不影响主答案
            log.warning("工具编排失败，改为纯节点执行：%s", type(exc).__name__)
            tool_results = []

    # 同一份数据在旧实现里会被取两遍：工具层取一次只用于警告，子任务节点再取一次。
    # 这里把成功的结果按 step_id 预置给子任务，省掉重复 DB/BM25/LLM 调用。
    by_step = {
        getattr(r, "step_id", ""): r for r in tool_results if getattr(r, "ok", False)
    }

    def prefetched_for(tid: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        # 走回退的结果不复用：降级值（空列表/None）不能覆盖节点原本可用的真实数据。
        generated = by_step.get(f"{tid}_sql")
        if generated is not None and not getattr(generated, "degraded", False) \
                and isinstance(generated.data, (list, tuple)) and len(generated.data) >= 2:
            out["sql"] = {"sql": generated.data[0], "source": generated.data[1]}
        executed = by_step.get(f"{tid}_run")
        if executed is not None and not getattr(executed, "degraded", False) \
                and isinstance(executed.data, list):
            out["rows"] = executed.data
        retrieved = by_step.get(f"{tid}_kb")
        if retrieved is not None and not getattr(retrieved, "degraded", False) \
                and isinstance(retrieved.data, list):
            out["hits"] = retrieved.data
        return out

    results: list[dict[str, Any]] = []
    for task in tasks:
        intent = str(task.get("intent", "smalltalk"))
        node_fn = _NODES.get(intent, smalltalk.smalltalk_node)
        sub_state: dict[str, Any] = {
            "question": task.get("question", ""),
            "advertiser_id": task.get("advertiser_id"),
            "intent": intent,
            "history": state.get("history", []),
            "trace": [],
            "retry": 0,
            "prefetched": prefetched_for(str(task.get("id", "t"))),
        }
        try:
            res = node_fn(sub_state, ctx)
        except Exception as exc:  # noqa: BLE001 - 子任务隔离，坏一个不影响其他
            log.exception("子任务 %s 执行失败", task.get("id"))
            err = AgentError(INTERNAL_ERROR, detail=type(exc).__name__, stage=str(task.get("id")))
            res = {"answer": err.message, "intent": intent, "evidence": {},
                   "citations": [], "trace": [], "review": {"ok": False, "issues": [err.code]}}
        results.append(res)
        for step in (res.get("trace") or []):
            trace.append({"step": f"{task.get('id')}/{step.get('step', 'node')}",
                          "detail": step.get("detail", "")})

    # 把计划还原成对象供 integrate 使用（无需依赖具体 Plan 类）
    from types import SimpleNamespace
    plan_view = SimpleNamespace(
        mode=plan.get("mode", "composite"),
        tasks=tasks,
        confidence=float(plan.get("confidence", 0.8)),
        clarify=plan.get("clarify"),
    )
    integrated = integrate(plan_view, results, tool_results)  # type: ignore[arg-type]
    failed = [r for r in tool_results if not getattr(r, "ok", False)]
    if failed:
        trace.append({"step": "tools", "detail": f"{len(failed)} 个工具失败，已使用降级结果"})

    evidence: dict[str, Any] = {
        "plan": plan,
        "tasks": [{"id": t.get("id"), "intent": t.get("intent"),
                   "advertiser_id": t.get("advertiser_id"),
                   "evidence": r.get("evidence", {})} for t, r in zip(tasks, results)],
        "tool_results": [r.to_dict() for r in tool_results],
    }
    return {
        "answer": integrated.render(),
        "intent": state.get("intent", tasks[0].get("intent") if tasks else "smalltalk"),
        "advertiser_id": state.get("advertiser_id"),
        "trace": trace,
        "evidence": evidence,
        "citations": integrated.citations,
        "structure": integrated.to_dict(),
        "confidence": integrated.confidence,
        "degraded": integrated.degraded,
        "warnings": integrated.warnings,
        "tool_results": evidence["tool_results"],
    }
