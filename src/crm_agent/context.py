"""运行时依赖容器。

节点函数不直接 new 数据库连接或模型客户端，而是从 Context 里取，
这样评估脚本可以注入 mock、API 服务可以复用同一个实例，避免每轮请求重建索引。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import Settings, load_settings
from .llm import LLM
from .memory import ConversationMemory
from .planning import Planner
from .store import CRMStore, open_store
from .tools.bootstrap import register_builtin_tools
from .tools.rag import KnowledgeBase
from .tools.registry import ToolRegistry

log = logging.getLogger("crm_agent.context")


class _EmptyKnowledgeBase:
    """知识库不可用时的空实现：检索返回空，让知识节点走'无命中'模板。"""

    def retrieve(self, query: str, top_k: int = 4, per_doc_limit: int = 2):
        return []

    def stats(self) -> dict:
        return {"chunks": 0, "docs": 0, "vocab": 0}


@dataclass
class Context:
    settings: Settings
    store: CRMStore
    kb: KnowledgeBase
    llm: LLM
    memory: ConversationMemory | None = None
    # 允许测试/外部代码用旧签名构造 Context，不强制提供工具层
    registry: ToolRegistry | None = None
    planner: Planner | None = None
    # LangChain 桥接门面：仅在 settings.langchain_enabled=true 时构建，
    # 关闭时为 None，主图/节点调用路径与旧版逐字节一致。
    langchain: Any | None = None
    # 客户名前 3 字 -> ID 的有序映射，避免每条问题都做一次全表排序查询
    advertiser_prefixes: dict[str, str] | None = None


def build_context(settings: Settings | None = None, use_llm: bool = True) -> Context:
    settings = settings or load_settings()
    store = open_store(settings.db_path)
    try:
        kb = KnowledgeBase(settings.knowledge_dir)
    except Exception as exc:  # noqa: BLE001 - 坏知识库不能让整个服务起不来
        log.warning("知识库加载失败，已降级为空知识库：%s", type(exc).__name__)
        kb = _EmptyKnowledgeBase()  # type: ignore[assignment]
    if use_llm:
        llm = LLM(settings)
    else:
        llm = LLM(Settings(api_key="", base_url="", model="offline",
                           db_path=settings.db_path, knowledge_dir=settings.knowledge_dir))
    memory = None
    if settings.memory_enabled:
        memory = ConversationMemory(
            max_turns=settings.memory_max_turns,
            max_sessions=settings.memory_max_sessions,
            answer_chars=settings.memory_answer_chars,
            db_path=settings.memory_db_path if settings.memory_persist else "",
        )

    # 统一工具层：所有能力先注册，再由规划器按名字取用。
    from types import SimpleNamespace

    registry = ToolRegistry()
    runtime = SimpleNamespace(store=store, kb=kb, llm=llm, settings=settings)
    register_builtin_tools(registry, runtime)
    registry.configure(timeout_s=settings.tool_timeout_s,
                       retries=settings.tool_max_retries,
                       fallback_enabled=settings.tool_fallback_enabled)

    planner = Planner(
        max_steps=settings.planning_max_steps,
        enabled=settings.planning_enabled,
    )

    # LangChain 桥接：默认关闭，不构建也不参与任何主链调用。
    langchain_bridge = None
    if settings.langchain_enabled:
        try:
            from .langchain_bridge import LangChainBridge
            langchain_bridge = LangChainBridge(
                llm=llm, registry=registry, ctx=SimpleNamespace(
                    store=store, kb=kb, llm=llm, settings=settings,
                ),
                temperature=settings.langchain_temperature,
                bind_tools=settings.langchain_bind_tools,
            )
        except Exception as exc:  # noqa: BLE001 - 桥接失败不能影响主服务启动
            log.warning("LangChain 桥接初始化失败，继续用原生路径：%s", type(exc).__name__)
            langchain_bridge = None

    # 客户名前缀映射在服务启动时构建一次，顺序与 router 原来的全表查询一致
    # （list_advertisers 按 spend_30d DESC），首个匹配保持原有指向。
    prefixes: dict[str, str] = {}
    try:
        for adv in store.list_advertisers(limit=42):
            prefixes.setdefault(adv["name"][:3], adv["advertiser_id"])
    except Exception:  # noqa: BLE001 - 映射只是加速手段，失败时 router 回退到原查询
        prefixes = {}

    return Context(settings=settings, store=store, kb=kb, llm=llm,
                   memory=memory, registry=registry, planner=planner,
                   langchain=langchain_bridge,
                   advertiser_prefixes=prefixes or None)
