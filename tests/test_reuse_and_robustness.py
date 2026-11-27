"""2026-11-27 优化回归：工具结果复用、ctx 注入判定、不可重试错误、连接与上下文。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from crm_agent.agent import SalesAgent  # noqa: E402
from crm_agent.config import Settings, load_settings  # noqa: E402
from crm_agent.context import build_context  # noqa: E402
from crm_agent.errors import INVALID_PARAMETERS  # noqa: E402
from crm_agent.memory import build_history_prompt  # noqa: E402
from crm_agent.tools import sql_agent  # noqa: E402
from crm_agent.tools.rag import KnowledgeBase  # noqa: E402
from crm_agent.tools.registry import ToolRegistry, ToolSpec  # noqa: E402

from test_daily_optimizations import offline_agent  # noqa: E402


class TestCompositeReuse(unittest.TestCase):
    def test_sql_generated_once_for_composite_analysis(self):
        agent = offline_agent()
        try:
            with mock.patch.object(sql_agent, "nl_to_sql", wraps=sql_agent.nl_to_sql) as gen:
                result = agent.ask("给我 A003 的客户画像，同时分析各行业消耗汇总",
                                   thread_id="reuse-analysis")
            self.assertEqual(result["plan"]["mode"], "composite")
            self.assertEqual(gen.call_count, 1)
        finally:
            agent.close()

    def test_knowledge_node_reuses_tool_hits(self):
        agent = offline_agent()
        try:
            with mock.patch.object(KnowledgeBase, "retrieve", wraps=KnowledgeBase.retrieve) as ret:
                result = agent.ask("帮我查一下团队SOP，同时分析各行业消耗汇总",
                                   thread_id="reuse-kb")
            intents = [t.get("intent") for t in result["plan"]["tasks"]]
            self.assertIn("knowledge", intents)
            # 检索由工具层完成；节点不再重复检索（patch 只覆盖节点实例调用）
            self.assertEqual(ret.call_count, 0)
        finally:
            agent.close()


class TestToolRegistryRobustness(unittest.TestCase):
    def test_ctx_detected_by_signature_not_exception_text(self):
        calls = []

        def handler(ctx, **params):
            calls.append(1)
            # 模拟 handler 内部抛出与旧判定文案相同的 TypeError
            raise TypeError("unexpected keyword argument 'ctx'")

        reg = ToolRegistry()
        reg.register(ToolSpec(name="bad-ctx", description="test", handler=handler,
                              parameters={}, retries=2))
        result = reg.call("bad-ctx", ctx=object())
        self.assertFalse(result.ok)
        self.assertEqual(len(calls), 1)

    def test_logic_error_is_not_retried(self):
        calls = []

        def handler():
            calls.append(1)
            raise ValueError("bad argument")

        reg = ToolRegistry()
        reg.register(ToolSpec(name="bad-arg", description="test", handler=handler,
                              parameters={}, retries=3))
        result = reg.call("bad-arg")
        self.assertEqual(result.error_code, INVALID_PARAMETERS)
        self.assertEqual(len(calls), 1)


class TestResourceAndContext(unittest.TestCase):
    def test_store_query_after_close_reopens_readonly(self):
        settings = load_settings()
        ctx = build_context(settings=Settings(**{**settings.__dict__, "memory_persist": False}),
                            use_llm=False)
        store = ctx.store
        try:
            store.close()
            rows = store.query("SELECT 1 AS ok")
            self.assertEqual(rows, [{"ok": 1}])
        finally:
            store.close()

    def test_long_turn_does_not_hide_earlier_context(self):
        turns = [
            {"role": "user", "intent": "analysis", "content": "早期的问题"},
            {"role": "assistant", "intent": "analysis", "content": "长回答" * 80},
            {"role": "user", "intent": "analysis", "content": "较新的问题"},
            {"role": "assistant", "intent": "analysis", "content": "短回答"},
        ]
        prompt = build_history_prompt(turns, max_turns=6, max_chars=200)
        self.assertIn("早期的问题", prompt)
        self.assertIn("较新的问题", prompt)


if __name__ == "__main__":
    unittest.main()
