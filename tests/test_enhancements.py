"""增强能力专项测试：工具框架 / 规划 / 离线韧性 / 结果整合 / 错误脱敏。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from crm_agent.agent import SalesAgent  # noqa: E402
from crm_agent.config import Settings, load_settings  # noqa: E402
from crm_agent.context import build_context  # noqa: E402
from crm_agent.errors import INVALID_PARAMETERS, TOOL_FAILED  # noqa: E402
from crm_agent.integration import integrate, number_mismatch  # noqa: E402
from crm_agent.planning import Planner, extract_slots, slots_prompt  # noqa: E402
from crm_agent.tools.bootstrap import P  # noqa: E402
from crm_agent.tools.registry import ToolRegistry, ToolSpec  # noqa: E402


def _agent() -> SalesAgent:
    base = load_settings()
    values = {**base.__dict__, "memory_persist": False, "memory_enabled": True}
    return SalesAgent(ctx=build_context(settings=Settings(**values), use_llm=False), use_llm=False)


class ToolRegistryTests(unittest.TestCase):
    def test_register_validate_and_success(self) -> None:
        reg = ToolRegistry()
        reg.register(ToolSpec(
            name="echo", description="回显",
            handler=lambda text: "got:" + text,
            parameters={"text": P("str", required=True, min_len=2)},
        ))
        ok = reg.call("echo", {"text": "ab"})
        self.assertTrue(ok.ok)
        self.assertEqual(ok.data, "got:ab")
        bad = reg.call("echo", {"text": "a"})
        self.assertFalse(bad.ok)
        self.assertEqual(bad.error_code, INVALID_PARAMETERS)

    def test_retry_then_success(self) -> None:
        calls = {"n": 0}

        def flaky() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("transient")
            return "ok"

        reg = ToolRegistry()
        reg.register(ToolSpec(name="flaky", description="", handler=flaky, retries=2))
        result = reg.call("flaky", {})
        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, 3)

    def test_fallback_marks_degraded(self) -> None:
        def broken() -> None:
            raise RuntimeError("down")

        reg = ToolRegistry()
        reg.register(ToolSpec(name="broken", description="", handler=broken,
                              fallback=lambda: "fallback-value"))
        result = reg.call("broken", {})
        self.assertTrue(result.ok)
        self.assertTrue(result.degraded)
        self.assertEqual(result.data, "fallback-value")

    def test_unregistered_tool_is_safe_failure(self) -> None:
        result = ToolRegistry().call("not.exist", {})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "tool_unavailable")

    def test_call_plan_resolves_dependencies(self) -> None:
        reg = ToolRegistry()
        reg.register(ToolSpec(name="make", description="", handler=lambda value: {"v": value * 2},
                              parameters={"value": P("int", required=True)}))
        reg.register(ToolSpec(name="consume", description="", handler=lambda v: v + 1,
                              parameters={"v": P("int", required=True)}))
        steps = [
            {"id": "a", "tool": "make", "params": {"value": 3}},
            {"id": "b", "tool": "consume", "params": {"v": {"$ref": "a.v"}}, "depends_on": ["a"]},
        ]
        results = reg.call_plan(steps)
        self.assertEqual(results[0].data, {"v": 6})
        self.assertEqual(results[1].data, 7)
        self.assertEqual([r.step_id for r in results], ["a", "b"])

    def test_plan_upstream_failure_blocks_dependent_step(self) -> None:
        reg = ToolRegistry()
        reg.register(ToolSpec(name="bad", description="", handler=lambda: (_ for _ in ()).throw(RuntimeError("x"))))
        results = reg.call_plan([
            {"id": "a", "tool": "bad", "params": {}},
            {"id": "b", "tool": "bad", "params": {}, "depends_on": ["a"]},
        ])
        self.assertTrue(all(not r.ok for r in results))
        self.assertEqual(results[0].error_code, TOOL_FAILED)
        self.assertEqual(results[1].error_code, "dependency_failed")

    def test_metrics_are_recorded(self) -> None:
        reg = ToolRegistry()
        reg.register(ToolSpec(name="ping", description="", handler=lambda: 1))
        reg.call("ping", {})
        self.assertEqual(reg.metrics()["per_tool"]["ping"]["calls"], 1)


class PlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = _agent()
        self.planner: Planner = self.agent.ctx.planner  # type: ignore[assignment]

    def tearDown(self) -> None:
        self.agent.close()

    def test_single_intent_plan(self) -> None:
        plan = self.planner.plan("A003 这个客户什么情况", "A003", [], self.agent.ctx,
                                 intent="profile")
        self.assertEqual(plan.mode, "single")
        self.assertEqual(len(plan.tasks), 1)
        self.assertIn("advertiser.get", plan.tasks[0].tools)

    def test_composite_plan_splits_tasks(self) -> None:
        plan = self.planner.plan(
            "给我 A003 的客户画像，同时分析各行业消耗汇总", "A003", [], self.agent.ctx,
            intent="profile",
        )
        self.assertEqual(plan.mode, "composite")
        self.assertEqual([t.intent for t in plan.tasks], ["profile", "analysis"])

    def test_followup_is_not_split(self) -> None:
        plan = self.planner.plan("还有呢", "A003", [], self.agent.ctx, intent="profile")
        self.assertEqual(plan.mode, "single")

    def test_slots_extract_context(self) -> None:
        slots = extract_slots([
            {"role": "user", "content": "看下 A003 的 ROAS"},
            {"role": "assistant", "content": "建议先谈续费", "intent": "profile",
             "advertiser_id": "A003"},
        ])
        self.assertEqual(slots["advertiser_id"], "A003")
        self.assertEqual(slots["intent"], "profile")
        self.assertIn("roas", slots["metrics"])
        self.assertIn("当前客户=A003", slots_prompt(slots))

    def test_agent_returns_plan_and_runs_composite(self) -> None:
        result = self.agent.ask("给我 A003 的客户画像，同时分析各行业消耗汇总",
                                thread_id="test-composite")
        self.assertEqual(result["plan"]["mode"], "composite")
        self.assertIn("客户画像", result["answer"])
        self.assertIn("经营分析", result["answer"])
        self.assertIsInstance(result["confidence"], float)
        self.assertTrue(result["tool_results"])


class OfflineResilienceTests(unittest.TestCase):
    def test_no_llm_still_answers(self) -> None:
        agent = _agent()
        try:
            self.assertFalse(agent.ctx.llm.available)
            probe = agent.ctx.registry.call("llm.probe", {})
            self.assertTrue(probe.ok)
            self.assertFalse(probe.data["available"])
            result = agent.ask("A003 这个客户什么情况", thread_id="offline-resilience")
            self.assertEqual(result["intent"], "profile")
            self.assertIn("A003", result["answer"])
        finally:
            agent.close()

    def test_tool_registry_failure_does_not_break_agent(self) -> None:
        agent = _agent()
        try:
            with patch.object(agent.ctx.registry, "call_plan",
                              side_effect=RuntimeError("registry down")):
                result = agent.ask("给我 A003 的客户画像，同时分析各行业消耗汇总",
                                   thread_id="tool-down")
            self.assertEqual(result["intent"], "profile")
            self.assertIn("客户画像", result["answer"])
        finally:
            agent.close()

    def test_sql_error_is_sanitized(self) -> None:
        agent = _agent()
        try:
            with patch("crm_agent.tools.sql_agent.run_readonly",
                       side_effect=RuntimeError("C:/secret/db.crm.db leaked")):
                result = agent.ask("各行业消耗汇总", thread_id="sql-error")
            self.assertNotIn("secret", result["answer"])
            self.assertNotIn("leaked", result["answer"])
            self.assertIn("查询执行失败", result["answer"])
        finally:
            agent.close()


class IntegrationTests(unittest.TestCase):
    def test_composite_render_has_sections(self) -> None:
        plan = SimpleNamespace(
            mode="composite",
            tasks=[{"id": "t1", "intent": "profile"}, {"id": "t2", "intent": "analysis"}],
            confidence=0.8, clarify=None,
        )
        answer = integrate(plan, [
            {"answer": "客户画像内容", "citations": []},
            {"answer": "分析内容", "citations": []},
        ], [])
        text = answer.render()
        self.assertIn("任务完成概览", text)
        self.assertIn("客户画像", text)
        self.assertIn("经营分析", text)
        self.assertEqual(len(answer.sections), 2)

    def test_number_mismatch_ignores_known_values(self) -> None:
        rows = [{"消耗合计": 362000.0, "客户数": 8}]
        self.assertEqual(number_mismatch("共 8 条记录，消耗 362,000 元", {"rows": rows}), [])
        self.assertIn("999,999", number_mismatch("消耗 999,999 元", {"rows": rows}))


if __name__ == "__main__":
    unittest.main()
