"""评估框架专项测试：验证器、失败归因、Pass^k 聚合、轨迹前缀动作分类。

对应 ai-agent-book 第 7 章的评估方法论，确保这些机制不是摆设：
每个测试都用构造的失败样例验证"检测器真的会报警"。
"""
import os
import sys
import unittest

EVALS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evals")
sys.path.insert(0, EVALS)

from verifiers import verify, verify_prefix, classify_action  # noqa: E402
from failure_attribution import attribute  # noqa: E402


def _base_case(**kw):
    case = {"id": "X", "question": "近45天消耗", "expect_intent": "analysis",
            "expect_keywords": ["45"], "expect_citation": False, "set": "boundary"}
    case.update(kw)
    return case


def _base_result(**kw):
    result = {"intent": "analysis", "answer": "不支持近45天，只有近30天口径",
              "citations": [], "review": {"ok": True}, "trace": [], "evidence": {}}
    result.update(kw)
    return result


class TestVerifiers(unittest.TestCase):
    def test_refusal_pass_when_scope_explained(self):
        case = _base_case(verifier="refusal", refusal_signals=["不支持", "口径"],
                          forbid_substrings=["1,464,090"])
        v = verify(case, _base_result())
        self.assertTrue(v["answer_ok"])
        self.assertTrue(v["pass"])

    def test_refusal_fails_when_fabricated_number_leaks(self):
        # 关键回归：即使说了"不支持"，只要漏出全库合计数字就必须判失败
        case = _base_case(verifier="refusal", refusal_signals=["不支持"],
                          forbid_substrings=["1,464,090"])
        result = _base_result(answer="不支持近45天，但合计 1,464,090 元")
        v = verify(case, result)
        self.assertFalse(v["answer_ok"])
        self.assertIn("1,464,090", v["forbid_hit"])

    def test_keywords_requires_all(self):
        case = _base_case(verifier="keywords", expect_keywords=["甲", "乙"])
        v = verify(case, _base_result(answer="只有甲"))
        self.assertFalse(v["answer_ok"])
        self.assertEqual(v["keyword_miss"], ["乙"])

    def test_unknown_verifier_fails_closed(self):
        # 未知验证器保守判失败，不能把"没验证"当成"通过"
        v = verify(_base_case(verifier="???", expect_keywords=[]), _base_result())
        self.assertFalse(v["answer_ok"])


class TestAttribution(unittest.TestCase):
    def test_pass_case_has_no_attribution(self):
        v = verify(_base_case(verifier="keywords"), _base_result())
        self.assertIsNone(attribute(v))

    def test_first_error_is_template_mismatch_not_last_step(self):
        fake = {
            "id": "H07", "question": "近45天", "verifier": "refusal", "pass": False,
            "intent_ok": True, "expect_intent": "analysis", "got_intent": "analysis",
            "answer_ok": False, "keyword_hit": 0, "keyword_total": 1, "keyword_miss": ["45"],
            "forbid_hit": ["1,464,090"], "citation_ok": True, "selfcheck_ok": True,
            "trace": [{"step": "router", "detail": "意图=analysis"},
                      {"step": "analysis", "detail": "生成 SQL（来源：template）"},
                      {"step": "analysis", "detail": "模型不可用，使用模板汇总查询结果"}],
            "evidence": {},
        }
        a = attribute(fake)
        self.assertEqual(a["category"], "口径边界未拦截")
        self.assertEqual(a["owner"], "harness/tool")
        self.assertEqual(a["first_error_step"], 1)  # SQL 生成步，非最后的模板步
        self.assertTrue(a["consequences"])

    def test_intent_error_attributed_to_router(self):
        fake = {
            "id": "X", "question": "q", "verifier": "keywords", "pass": False,
            "intent_ok": False, "expect_intent": "analysis", "got_intent": "profile",
            "answer_ok": True, "keyword_hit": 1, "keyword_total": 1, "keyword_miss": [],
            "forbid_hit": [], "citation_ok": True, "selfcheck_ok": True,
            "trace": [], "evidence": {},
        }
        a = attribute(fake)
        self.assertEqual(a["category"], "意图路由错误")
        self.assertEqual(a["owner"], "harness/router")


class TestActionClassification(unittest.TestCase):
    def test_refusal_detected_from_evidence(self):
        r = _base_result(evidence={"unsupported_window": "45天"})
        self.assertEqual(classify_action(r), "refuse_with_scope")

    def test_monthly_query_detected_from_sql(self):
        # 结论文本里即使含"口径"等词，也不能误判为拒绝——只看 SQL
        r = _base_result(answer="整体变好，主要矛盾看口径ROAS",
                         evidence={"sql": "SELECT m.month FROM metrics m WHERE ..."})
        self.assertEqual(classify_action(r), "query_monthly_metrics")

    def test_prefix_forbid_substring_veto(self):
        case = {"id": "PFX", "scenario": "边界", "acceptable_actions": ["refuse_with_scope"],
                "forbidden_actions": [], "forbid_substrings": ["1,464,090"]}
        r = _base_result(answer="不支持，但合计 1,464,090", evidence={"unsupported_window": "45天"})
        p = verify_prefix(case, r)
        self.assertFalse(p["pass"])  # 动作对，但禁止子串命中 → 一票否决


if __name__ == "__main__":
    unittest.main()
