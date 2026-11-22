"""上下文记忆专项测试。

运行：python -m unittest discover -s tests -v
覆盖：指代消解、意图继承、追问 SQL 补全、批量问题不被误绑定、
      轮数/会话上限、SQLite 持久化重启、API 多轮与清除。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from crm_agent.agent import SalesAgent  # noqa: E402
from crm_agent.config import Settings, load_settings  # noqa: E402
from crm_agent.context import build_context  # noqa: E402
from crm_agent.memory import ConversationMemory  # noqa: E402
from crm_agent.nodes.router import _is_followup, has_intent_signal  # noqa: E402


def offline_agent(**memory_overrides) -> SalesAgent:
    """构造不落盘、纯离线的 Agent，避免测试污染真实记忆库。"""
    s = load_settings()
    overrides = dict(memory_persist=False, use_checkpointer=False, llm_mode="offline")
    overrides.update(memory_overrides)
    s = Settings(**{**s.__dict__, **overrides})
    return SalesAgent(ctx=build_context(settings=s, use_llm=False), use_llm=False)


class ReferentResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = offline_agent()

    @classmethod
    def tearDownClass(cls):
        cls.agent.close()

    def setUp(self):
        self.sid = "test-" + self.id()

    def test_01_followup_detection(self):
        self.assertTrue(_is_followup("他现在怎么样"))
        self.assertTrue(_is_followup("这家客户画像"))
        self.assertTrue(_is_followup("继续"))
        self.assertTrue(_is_followup("还有呢"))
        self.assertTrue(_is_followup("那转化率呢"))
        # 复数/批量不该被当成追问
        self.assertFalse(_is_followup("其他客户呢"))
        self.assertFalse(_is_followup("转化率最低的10个客户"))
        self.assertFalse(has_intent_signal("继续"))
        self.assertTrue(has_intent_signal("转化率最低的10个客户"))

    def test_02_anaphora_keeps_advertiser(self):
        r1 = self.agent.ask("A003 现在有什么商机", thread_id=self.sid)
        self.assertEqual(r1["advertiser_id"], "A003")
        r2 = self.agent.ask("他的客户画像", thread_id=self.sid)
        self.assertEqual(r2["advertiser_id"], "A003")
        self.assertEqual(r2["intent"], "profile")
        self.assertIn("A003", r2["answer"])

    def test_03_continuation_keeps_intent(self):
        self.agent.ask("A003 的客户画像", thread_id=self.sid)
        r = self.agent.ask("继续", thread_id=self.sid)
        self.assertEqual(r["intent"], "profile")
        self.assertEqual(r["advertiser_id"], "A003")
        self.assertIn("沿用上一轮意图", " ".join(t["detail"] for t in r["trace"]))

    def test_04_batch_not_bound_to_previous_client(self):
        self.agent.ask("A003 的客户画像", thread_id=self.sid)
        r = self.agent.ask("转化率最低的10个客户", thread_id=self.sid)
        self.assertIsNone(r["advertiser_id"])
        self.assertEqual(r["intent"], "analysis")

    def test_05_analysis_followup_completes_question(self):
        self.agent.ask("转化率最低的10个客户", thread_id=self.sid)
        r = self.agent.ask("还有呢", thread_id=self.sid)
        self.assertEqual(r["intent"], "analysis")
        self.assertNotIn("暂时没法转成查询", r["answer"])
        self.assertIn("追问补全", " ".join(t["detail"] for t in r["trace"]))

    def test_06_sessions_are_isolated(self):
        self.agent.ask("A003 的客户画像", thread_id=self.sid + "-a")
        r = self.agent.ask("他的画像", thread_id=self.sid + "-b")
        # 另一个会话没有历史，指代词无从解析，不应绑到 A003
        self.assertIsNone(r["advertiser_id"])


class MemoryStoreTests(unittest.TestCase):
    def test_turn_and_session_caps(self):
        m = ConversationMemory(max_turns=4, max_sessions=3)
        for i in range(5):
            m.append_turn("s1", "q" + str(i), "a" * 50, "profile", "A003")
        self.assertEqual(len(m.get("s1")), 4)
        for i in range(6):
            m.append_turn("s" + str(i), "q", "a", "profile", None)
        self.assertLessEqual(m.stats()["sessions"], 3)
        m.close()

    def test_persistence_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "mem.db")
            m1 = ConversationMemory(max_turns=4, db_path=path)
            for i in range(3):
                m1.append_turn("s1", "问题" + str(i), "答案" * 30, "analysis", None)
            before = [t.content for t in m1.get("s1")]
            m1.close()

            m2 = ConversationMemory(max_turns=4, db_path=path)
            after = [t.content for t in m2.get("s1")]
            self.assertEqual(before, after)
            self.assertEqual(len(after), 4)
            m2.append_turn("s1", "问题3", "答案3" * 30, "analysis", None)
            m2.close()

            m3 = ConversationMemory(max_turns=4, db_path=path)
            self.assertEqual(len(m3.get("s1")), 4)
            self.assertIn("问题3", [t.content for t in m3.get("s1")])
            m3.close()

    def test_clear(self):
        m = ConversationMemory()
        m.append_turn("s1", "q", "a", "profile", None)
        self.assertTrue(m.clear("s1"))
        self.assertEqual(m.get("s1"), [])
        m.close()


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            raise unittest.SkipTest("fastapi 未安装")
        os.environ["LLM_MODE"] = "offline"
        os.environ["USE_CHECKPOINTER"] = "false"
        os.environ["MEMORY_PERSIST"] = "false"
        import api.server as server
        cls.client = TestClient(server.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_chat_history_clear(self):
        sid = "api-test-session"
        for q in ["A003 有什么商机", "他的画像"]:
            r = self.client.post("/api/chat", json={"question": q, "session_id": sid})
            self.assertEqual(r.status_code, 200)
        h = self.client.get("/api/history", params={"session_id": sid}).json()
        self.assertEqual(len(h["turns"]), 4)
        self.assertEqual(self.client.delete("/api/history", params={"session_id": sid}).json()["cleared"], True)
        h2 = self.client.get("/api/history", params={"session_id": sid}).json()
        self.assertEqual(len(h2["turns"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
