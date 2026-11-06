"""运行时依赖容器。

节点函数不直接 new 数据库连接或模型客户端，而是从 Context 里取，
这样评估脚本可以注入 mock、API 服务可以复用同一个实例，避免每轮请求重建索引。
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings, load_settings
from .llm import LLM
from .store import CRMStore, open_store
from .tools.rag import KnowledgeBase


@dataclass
class Context:
    settings: Settings
    store: CRMStore
    kb: KnowledgeBase
    llm: LLM


def build_context(settings: Settings | None = None, use_llm: bool = True) -> Context:
    settings = settings or load_settings()
    store = open_store(settings.db_path)
    kb = KnowledgeBase(settings.knowledge_dir)
    if use_llm:
        llm = LLM(settings)
    else:
        llm = LLM(Settings(api_key="", base_url="", model="offline",
                           db_path=settings.db_path, knowledge_dir=settings.knowledge_dir))
    return Context(settings=settings, store=store, kb=kb, llm=llm)
