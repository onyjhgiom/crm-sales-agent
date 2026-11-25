"""2026-11-26 优化的回归测试：超时记忆、编排参数、SQL 白名单、缓存、日期与暂存安全。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from crm_agent.agent import SalesAgent  # noqa: E402
from crm_agent.config import Settings, load_settings  # noqa: E402
from crm_agent.context import build_context  # noqa: E402
from crm_agent.tools import scoring, sql_agent  # noqa: E402
import daily_update  # noqa: E402


def offline_context():
    settings = load_settings()
    settings = Settings(**{**settings.__dict__, "memory_persist": False,
                           "use_checkpointer": False, "llm_mode": "offline"})
    return build_context(settings=settings, use_llm=False)


def offline_agent() -> SalesAgent:
    return SalesAgent(ctx=offline_context(), use_llm=False)


class TestSQLGuardHardening(unittest.TestCase):
    def test_comma_separated_illegal_table_is_rejected(self):
        ok, reason = sql_agent.guard("SELECT * FROM advertisers, sqlite_master")
        self.assertFalse(ok)
        self.assertIn("sqlite_master", reason)

    def test_join_illegal_table_is_rejected(self):
        ok, reason = sql_agent.guard(
            "SELECT a.name FROM advertisers a JOIN sqlite_master b ON a.id=b.id"
        )
        self.assertFalse(ok)
        self.assertIn("sqlite_master", reason)

    def test_legal_queries_with_replace_like_columns_pass(self):
        for sql in (
            "SELECT replaced_at FROM advertisers",
            "SELECT created_at, name FROM advertisers WHERE name='from sqlite_master'",
            "SELECT a.name, c.spend_30d FROM advertisers a JOIN campaigns c ON a.advertiser_id=c.advertiser_id",
        ):
            with self.subTest(sql=sql):
                ok, _ = sql_agent.guard(sql)
                self.assertTrue(ok)

    def test_sql_injection_keywords_still_rejected(self):
        ok, _ = sql_agent.guard("REPLACE INTO advertisers VALUES (1)")
        self.assertFalse(ok)
        ok, _ = sql_agent.guard("SELECT * FROM advertisers; DELETE FROM campaigns")
        self.assertFalse(ok)


class TestDateAgainstLongRunningProcess(unittest.TestCase):
    def test_dates_use_current_day_at_call_time(self):
        fake = dt.date(2030, 1, 1)
        with mock.patch.object(scoring, "_today", return_value=fake):
            self.assertEqual(scoring.days_since("2029-12-30"), 2)
            self.assertEqual(scoring.days_until("2030-01-10"), 9)

    def test_invalid_date_still_degrades(self):
        self.assertEqual(scoring.days_since("not-a-date"), 999)
        self.assertEqual(scoring.days_until("not-a-date"), 999)


class TestCachedAdvertiserPrefixes(unittest.TestCase):
    def test_cache_matches_original_scan_order(self):
        ctx = offline_context()
        try:
            expected = {}
            for adv in ctx.store.list_advertisers(limit=42):
                expected.setdefault(adv["name"][:3], adv["advertiser_id"])
            self.assertEqual(ctx.advertiser_prefixes, expected)
        finally:
            ctx.store.close()

    def test_router_uses_cache_and_keeps_same_answer(self):
        from crm_agent.nodes import router
        from crm_agent.state import AgentState

        ctx = offline_context()
        try:
            first = next(iter((ctx.advertiser_prefixes or {}).items()))
            prefix, adv_id = first
            state: AgentState = {
                "question": f"帮我看下{prefix}的情况", "advertiser_id": None,
                "history": [], "trace": [], "retry": 0,
            }
            self.assertEqual(router.resolve_advertiser(state, ctx), adv_id)
        finally:
            ctx.store.close()


class TestNoGhostMemoryAfterTimeout(unittest.TestCase):
    def test_cancelled_request_does_not_append_turn(self):
        agent = offline_agent()
        try:
            session = "cancelled-test"
            cancelled = SimpleNamespace(is_set=lambda: True)
            result = agent.ask("各行业的消耗和 ROAS 汇总一下", session, None, cancelled)
            self.assertEqual(result.get("memory_skipped"), "request_cancelled")
            self.assertEqual(agent.history(session), [])
        finally:
            agent.close()

    def test_normal_request_still_appends_turn(self):
        agent = offline_agent()
        try:
            session = "normal-test"
            cancelled = SimpleNamespace(is_set=lambda: False)
            agent.ask("各行业的消耗和 ROAS 汇总一下", session, None, cancelled)
            turns = agent.history(session)
            self.assertEqual([t["role"] for t in turns], ["user", "assistant"])
        finally:
            agent.close()


class TestCompositeWithoutAdvertiser(unittest.TestCase):
    def test_composite_analysis_without_customer_does_not_fail_validation(self):
        agent = offline_agent()
        try:
            result = agent.ask(
                "介绍一下业务背景，同时分析各行业消耗汇总", thread_id="composite-no-adv"
            )
            self.assertNotEqual(result.get("intent"), "error")
            self.assertTrue(result.get("answer"))
            failures = [t for t in (result.get("tool_results") or [])
                        if t.get("error_code") == "invalid_parameters"]
            self.assertEqual(failures, [])
        finally:
            agent.close()


class TestScopedGitReset(unittest.TestCase):
    def test_reset_command_only_targets_task_paths(self):
        calls: list[list[str]] = []
        with mock.patch.object(daily_update, "run_cmd", side_effect=lambda cmd, *a, **k: (
            calls.append(cmd), SimpleNamespace(returncode=0, stdout="", stderr="")
        )[1]):
            daily_update.reset_staged(["src/a.py", "docs/b.md"], {"git_timeout_s": 10})
        self.assertEqual(calls[0][:3], ["git", "reset", "--"])
        self.assertEqual(calls[0][3:], ["src/a.py", "docs/b.md"])


if __name__ == "__main__":
    unittest.main()
