"""把现有本地能力注册成统一工具。

只做"包装"，不改写业务逻辑：handler 直接指向原来的 store/kb/sql_agent/scoring 方法，
因此注册前后同一问题、同一数据的答案逐字节不变；增强的是调用方式与失败语义。
新增外部工具（HTTP、飞书、行业基准库等）只需在这里多注册一条 ToolSpec。
"""

from __future__ import annotations

from typing import Any

from ..errors import DATA_UNAVAILABLE, UNSAFE_OPERATION, AgentError
from . import scoring, sql_agent
from .registry import ToolRegistry, ToolSpec


def P(kind: str, description: str = "", **rules: Any) -> dict[str, Any]:
    """构造一个轻量参数约束（见 registry.validate_params）。"""
    return {"type": kind, "description": description, **rules}


_ID = P("str", "客户编号，如 A003", required=True, pattern=r"^A\d{3}$")
_LIMIT = P("int", "返回条数", default=50, min=1, max=200)


def register_builtin_tools(registry: ToolRegistry, ctx: Any) -> ToolRegistry:
    """注册项目内置工具；重复调用是幂等的（已存在则跳过）。"""
    store, kb, llm = ctx.store, ctx.kb, ctx.llm

    def add(spec: ToolSpec) -> None:
        if registry.get(spec.name) is None:
            registry.register(spec)

    # ---- 数据访问（本地 SQLite，零网络）----
    add(ToolSpec(
        name="advertiser.get", description="按客户编号取客户档案",
        handler=store.get_advertiser, parameters={"advertiser_id": _ID},
        tags=("crm", "read"),
    ))
    add(ToolSpec(
        name="advertiser.list", description="列出客户（按消耗排序）",
        handler=store.list_advertisers, parameters={"limit": _LIMIT},
        tags=("crm", "read"),
    ))
    add(ToolSpec(
        name="advertiser.search", description="按客户名关键词搜索",
        handler=store.search_advertisers,
        parameters={"keyword": P("str", "客户名片段", required=True, min_len=1, max_len=50),
                    "limit": P("int", "返回条数", default=5, min=1, max=50)},
        tags=("crm", "read"),
    ))
    add(ToolSpec(
        name="campaign.list", description="取客户的投放计划",
        handler=store.get_campaigns, parameters={"advertiser_id": _ID},
        tags=("crm", "read"),
    ))
    add(ToolSpec(
        name="metrics.list", description="取客户的月度指标",
        handler=store.get_metrics,
        parameters={"advertiser_id": _ID, "limit": P("int", "月数", default=3, min=1, max=36)},
        tags=("crm", "read"),
    ))
    add(ToolSpec(
        name="interaction.list", description="取客户的跟进记录",
        handler=store.get_interactions,
        parameters={"advertiser_id": _ID, "limit": P("int", "条数", default=5, min=1, max=50)},
        tags=("crm", "read"),
    ))

    # ---- 知识检索（本地 BM25，零网络）----
    add(ToolSpec(
        name="kb.retrieve", description="从团队 SOP/案例中检索相关片段",
        handler=kb.retrieve,
        parameters={
            "query": P("str", "检索问题", required=True, min_len=1, max_len=500),
            "top_k": P("int", "命中段数", default=4, min=1, max=20),
            "per_doc_limit": P("int", "单篇最多段数", default=2, min=1, max=5),
        },
        fallback=lambda **_: [],                 # 索引异常时返回空，节点再走"无命中"模板
        tags=("knowledge", "local"),
    ))
    add(ToolSpec(
        name="kb.stats", description="知识库规模统计",
        handler=kb.stats, parameters={}, fallback=lambda **_: {"chunks": 0, "docs": 0},
        tags=("knowledge", "local"),
    ))

    # ---- 业务规则（本地纯函数）----
    add(ToolSpec(
        name="scoring.risk", description="识别客户风险信号并分级",
        handler=_risk_wrapper,
        parameters={
            "adv": P("dict", "客户档案", required=True),
            "campaigns": P("list", "计划列表", required=True),
            "metrics": P("list", "月度指标", required=True),
            "interactions": P("list", "跟进记录", required=True),
        },
        tags=("scoring", "rule"),
    ))
    add(ToolSpec(
        name="scoring.opportunities", description="按规则给客户排商机优先级",
        handler=scoring.score_opportunities,
        parameters={
            "adv": P("dict", "客户档案", required=True),
            "campaigns": P("list", "计划列表", required=True),
            "metrics": P("list", "月度指标", required=True),
            "interactions": P("list", "跟进记录", required=True),
            "signals": P("list", "风险信号", required=True),
        },
        tags=("scoring", "rule"),
    ))

    # ---- Text-to-SQL（可选外部模型 + 模板兜底）----
    add(ToolSpec(
        name="sql.generate", description="把自然语言问题转成只读 SELECT",
        handler=_sql_generate(ctx),
        parameters={
            "question": P("str", "分析问题", required=True, min_len=1, max_len=500),
            "advertiser_id": P("str", "限定客户", pattern=r"^A\d{3}$"),
        },
        fallback=lambda **_: (None, "none"),
        source="optional-llm", tags=("sql", "analysis"),
    ))
    add(ToolSpec(
        name="sql.execute", description="安全校验后只读执行 SQL",
        handler=_sql_execute(ctx),
        parameters={"sql": P("str", "SELECT 语句", required=True, min_len=1, max_len=4000),
                    "limit": P("int", "最多返回行", default=50, min=1, max=200)},
        fallback=lambda **_: [],
        tags=("sql", "analysis"),
    ))

    # ---- 外部服务探针：模型不可用时返回明确状态，节点可据此降级 ----
    add(ToolSpec(
        name="llm.probe", description="检查外部模型是否可用（不发实际生成请求）",
        handler=lambda: {"available": bool(llm and llm.available),
                         "mode": ctx.settings.mode},
        parameters={}, tags=("system", "optional"),
    ))
    return registry


# ---- 包装函数 -------------------------------------------------------------
def _risk_wrapper(adv: dict, campaigns: list, metrics: list, interactions: list) -> dict:
    signals = scoring.risk_signals(adv, campaigns, metrics, interactions)
    return {
        "signals": signals,
        "level": scoring.risk_level(signals),
        "health": scoring.health_score(adv, signals),
    }


def _sql_generate(ctx: Any):
    def handler(question: str, advertiser_id: str | None = None):
        return sql_agent.nl_to_sql(question, ctx.llm, advertiser_id)
    return handler


def _sql_execute(ctx: Any):
    def handler(sql: str, limit: int = 50):
        ok, reason = sql_agent.guard(sql)
        if not ok:
            raise AgentError(UNSAFE_OPERATION, detail=reason, stage="sql.guard")
        try:
            return sql_agent.run_readonly(ctx.store.db_path, sql, limit=limit)
        except Exception as exc:  # noqa: BLE001
            raise AgentError(DATA_UNAVAILABLE, detail=type(exc).__name__, stage="sql.execute") from exc
    return handler
