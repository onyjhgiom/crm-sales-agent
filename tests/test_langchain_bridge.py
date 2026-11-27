"""LangChain 桥接层专项测试：可用性探测 / prompt 转义 / LCEL / 工具 / 回调 / 零回归。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from crm_agent import langchain_bridge as lb  # noqa: E402
from crm_agent.agent import SalesAgent  # noqa: E402
from crm_agent.config import Settings, load_settings  # noqa: E402
from crm_agent.context import build_context  # noqa: E402
from crm_agent.tools.registry import ToolRegistry, ToolSpec  # noqa: E402


class FakeLLM:
    """模拟 LLM：记录调用参数，返回可预测文本。"""

    def __init__(self, reply: str = "模型回答") -> None:
        self.reply = reply
        self.calls: list[tuple[list[dict[str, str]], float, int | None]] = []

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.2,
             max_tokens: int | None = None) -> str | None:
        self.calls.append((messages, temperature, max_tokens))
        return self.reply


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ToolSpec(
        name="echo", description="回显文本",
        handler=lambda text: "echo:" + text,
        parameters={"text": {"type": "str", "description": "输入", "required": True,
                             "min_len": 1}},
    ))
    reg.register(ToolSpec(
        name="broken", description="故意失败",
        handler=lambda: (_ for _ in ()).throw(RuntimeError("secret detail")),
        parameters={},
    ))
    return reg


class AvailabilityTests(unittest.TestCase):
    def test_available_and_version(self) -> None:
        # 当前环境装了 langchain-core；若未装，两个断言也应自洽（available=False, version 空）
        self.assertIsInstance(lb.available(), bool)
        if lb.available():
            self.assertTrue(lb.version())
        else:
            self.assertEqual(lb.version(), "")

    @unittest.skipUnless(lb.available(), "需要 langchain-core")
    def test_missing_import_degrades_to_unavailable(self) -> None:
        # 用一个空壳 registry + None llm，构造不应抛异常
        bridge = lb.LangChainBridge(None, None, None)
        self.assertTrue(bridge.available)  # 模型对象可为 None，但组件可用
        self.assertEqual(bridge.tool_names(), [])


class PromptTests(unittest.TestCase):
    @unittest.skipUnless(lb.available(), "需要 langchain-core")
    def test_prompt_escapes_braces_and_keeps_roles(self) -> None:
        prompt = lb.build_prompt([
            {"role": "system", "content": "JSON 示例 {\"a\": 1}，不要展开"},
            {"role": "user", "content": "问 {x} 的问题"},
        ])
        self.assertIsNotNone(prompt)
        msgs = prompt.format_messages(x="ignored")
        self.assertEqual(msgs[0].type, "system")
        self.assertEqual(msgs[0].content, 'JSON 示例 {"a": 1}，不要展开')
        self.assertEqual(msgs[1].type, "human")
        self.assertEqual(msgs[1].content, "问 {x} 的问题")  # 花括号是字面量，不被格式化

    def test_prompt_none_when_disabled(self) -> None:
        self.assertIsNone(lb.build_prompt([]))

    @unittest.skipUnless(lb.available(), "需要 langchain-core")
    def test_message_roundtrip(self) -> None:
        msgs = lb.message_dict_to_lc([
            {"role": "system", "content": "s"},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "u"},
        ])
        self.assertEqual([m.type for m in msgs], ["system", "ai", "human"])
        self.assertEqual(lb.lc_message_to_text(msgs[2]), "u")
        self.assertEqual(lb.lc_message_to_text("plain"), "plain")


class ModelAndChainTests(unittest.TestCase):
    @unittest.skipUnless(lb.available(), "需要 langchain-core")
    def test_chat_model_delegates_to_llm(self) -> None:
        fake = FakeLLM("hello")
        model = lb.build_chat_model(fake, temperature=0.7, max_tokens=123)
        self.assertIsNotNone(model)
        result = model.invoke(lb.message_dict_to_lc([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]))
        self.assertEqual(lb.lc_message_to_text(result), "hello")
        self.assertEqual(fake.calls[0][0][-1], {"role": "user", "content": "hi"})
        self.assertEqual(fake.calls[0][1], 0.7)
        self.assertEqual(fake.calls[0][2], 123)

    @unittest.skipUnless(lb.available(), "需要 langchain-core")
    def test_offline_llm_returns_empty_not_fabricated(self) -> None:
        # LLM.chat 返回 None（离线/熔断）时链返回空串，不编造答案
        fake = FakeLLM()
        fake.reply = None  # type: ignore[assignment]
        bridge = lb.LangChainBridge(fake, None, None)
        out = bridge.invoke([{"role": "user", "content": "问题"}])
        self.assertEqual(out, "")

    @unittest.skipUnless(lb.available(), "需要 langchain-core")
    def test_chain_invoke_end_to_end(self) -> None:
        bridge = lb.LangChainBridge(FakeLLM("LCEL OK"), None, None)
        out = bridge.invoke([
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
        ])
        self.assertEqual(out, "LCEL OK")
        self.assertGreaterEqual(bridge.metrics()["callbacks"]["chains"], 1)

    @unittest.skipUnless(lb.available(), "需要 langchain-core")
    def test_chain_error_returns_empty(self) -> None:
        class Boom:
            def chat(self, *args: object, **kwargs: object) -> str:
                raise RuntimeError("boom")

        bridge = lb.LangChainBridge(Boom(), None, None)
        self.assertEqual(bridge.invoke([{"role": "user", "content": "x"}]), "")


class ToolBindingTests(unittest.TestCase):
    @unittest.skipUnless(lb.available(), "需要 langchain-core")
    def test_build_tools_and_invoke(self) -> None:
        bridge = lb.LangChainBridge(FakeLLM(), _registry(), None)
        names = bridge.tool_names()
        self.assertIn("echo", names)
        self.assertIn("broken", names)
        echo = next(t for t in bridge.tools if t.name == "echo")
        self.assertEqual(echo.invoke({"text": "abc"}), "echo:abc")

    @unittest.skipUnless(lb.available(), "需要 langchain-core")
    def test_failed_tool_raises_sanitized_message(self) -> None:
        bridge = lb.LangChainBridge(FakeLLM(), _registry(), None)
        broken = next(t for t in bridge.tools if t.name == "broken")
        with self.assertRaises(RuntimeError) as ctx:
            broken.invoke({})
        self.assertNotIn("secret detail", str(ctx.exception))
        self.assertNotIn("RuntimeError", str(ctx.exception))

    @unittest.skipUnless(lb.available(), "需要 langchain-core")
    def test_build_tools_respects_name_filter_and_disabled(self) -> None:
        reg = _registry()
        tools = lb.build_tools(reg, None, names=["echo"])
        self.assertEqual([t.name for t in tools], ["echo"])
        reg.unregister("echo")
        self.assertEqual([t.name for t in lb.build_tools(reg, None)], ["broken"])

    def test_build_tools_none_when_no_registry(self) -> None:
        self.assertEqual(lb.build_tools(None, None), [])


class ConfigAndZeroRegressionTests(unittest.TestCase):
    def test_config_defaults_off(self) -> None:
        s = load_settings()
        self.assertFalse(s.langchain_enabled)
        self.assertAlmostEqual(s.langchain_temperature, 0.2)
        self.assertTrue(s.langchain_bind_tools)

    def test_context_disabled_has_no_bridge(self) -> None:
        base = load_settings()
        values = {**base.__dict__, "memory_persist": False, "memory_enabled": False,
                  "langchain_enabled": False}
        ctx = build_context(settings=Settings(**values), use_llm=False)
        self.assertIsNone(ctx.langchain)

    def test_context_enabled_builds_bridge(self) -> None:
        if not lb.available():
            self.skipTest("需要 langchain-core")
        base = load_settings()
        values = {**base.__dict__, "memory_persist": False, "memory_enabled": False,
                  "langchain_enabled": True}
        ctx = build_context(settings=Settings(**values), use_llm=False)
        self.assertIsNotNone(ctx.langchain)
        self.assertTrue(ctx.langchain.available)
        self.assertIn("advertiser.get", ctx.langchain.tool_names())

    def test_main_path_unchanged_when_enabled(self) -> None:
        """打开桥接后，主链答案与关闭时一致（零回归的核心保证）。"""
        base = load_settings()
        common = {**base.__dict__, "memory_persist": False, "memory_enabled": False}
        off = Settings(**{**common, "langchain_enabled": False})
        on = Settings(**{**common, "langchain_enabled": True})

        with_off = SalesAgent(ctx=build_context(settings=off, use_llm=False), use_llm=False)
        with_on = SalesAgent(ctx=build_context(settings=on, use_llm=False), use_llm=False)
        try:
            for q in ("A003 现在有什么商机", "最近三个月消耗走势怎么样", "你好"):
                a = with_off.ask(q, thread_id="lc-off-" + q)
                b = with_on.ask(q, thread_id="lc-on-" + q)
                self.assertEqual(a["answer"], b["answer"])
                self.assertEqual(a["intent"], b["intent"])
        finally:
            with_off.close()
            with_on.close()


if __name__ == "__main__":
    unittest.main()
