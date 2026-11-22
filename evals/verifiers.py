"""评估验证器：核实机器可独立复核的事实，而非 Agent 的自我陈述。

设计依据（ai-agent-book 第 7 章「验证器」）：
- SWE-bench 把"已修复"拆成 FAIL_TO_PASS 与 PASS_TO_PASS 两个独立命题；
- OSWorld 不只确认报告文件存在，还连数据库核实动作是否真的生效；
- 核心原则：验证器检查可复核的客观事实，不采信 Agent 自己写的"已完成"。

本项目把验证器抽成独立模块，支持三类：
- keywords：答案必须命中全部期望关键词（基础事实核对）；
- refusal：特定边界问题必须给出"拒绝/说明口径"的回答，且不得出现被禁止的
  编造数字——这正是 H07 从"答非所问还给数字"修过来后要守住的行为；
- contains_not：答案不得包含某些内容（防退化，如把全库合计数字误报给用户）。

每条用例还可标注所属集合（对应"边界集 + 保留集"）：
- retention（保留集）：本轮改动不应影响、必须保持逐字节一致的用例；
- boundary（边界集）：本轮改动专门要修正的用例（如 H07）。
任何一次修改都要同时在两集合上验证：只测边界会把过拟合当成进步，
只测保留会把无效修改当成安全。
"""

from __future__ import annotations

from typing import Any


def _answer_ok(case: dict, answer: str) -> bool:
    """按用例声明的验证器类型，核对答案。返回是否通过。"""
    kind = case.get("verifier", "keywords")

    if kind == "keywords":
        low = answer.lower()
        return all(k.lower() in low for k in case.get("expect_keywords", []))

    if kind == "refusal":
        low = answer.lower()
        # 必须出现拒绝信号之一：明确说"不支持/没法/无法/不能/暂时"，或给出口径边界
        must_one = case.get("refusal_signals", ["不支持", "没法", "无法", "不能", "暂时", "只有", "口径"])
        if not any(s.lower() in low for s in must_one):
            return False
        # 必须命中该边界的识别证据（如 H07 必须识别出"45天"）
        if case.get("expect_keywords") and not all(
            k.lower() in low for k in case["expect_keywords"]
        ):
            return False
        # 最关键：不得出现被禁止的编造数字（如误报的全库消耗合计）
        if any(bad.lower() in low for bad in case.get("forbid_substrings", [])):
            return False
        return True

    # 未知验证器类型：保守判失败，避免"没验证"被当成"通过"
    return False


def verify(case: dict, result: dict) -> dict:
    """跑单条用例的全部验证维度，返回结构化结果（含失败归因所需证据）。

    返回字段：
      intent_ok / answer_ok / citation_ok / selfcheck_ok / pass
      set: 边界集还是保留集
      evidence: 便于复盘的客观证据（命中/缺失的关键词、被禁止的内容等）
    """
    answer = result.get("answer", "")
    low = answer.lower()
    expect_kw = case.get("expect_keywords", [])
    kw_hit = [k for k in expect_kw if k.lower() in low]
    kw_miss = [k for k in expect_kw if k not in kw_hit]

    forbid_hit = [b for b in case.get("forbid_substrings", []) if b.lower() in low]

    intent_ok = result.get("intent") == case.get("expect_intent")
    answer_ok = _answer_ok(case, answer)
    citations = result.get("citations") or []
    citation_ok = (not case.get("expect_citation")) or bool(citations)
    selfcheck_ok = bool((result.get("review") or {}).get("ok"))

    return {
        "id": case["id"],
        "question": case.get("question", ""),
        "verifier": case.get("verifier", "keywords"),
        "set": case.get("set", "retention"),
        "intent_ok": intent_ok,
        "expect_intent": case.get("expect_intent"),
        "got_intent": result.get("intent"),
        "answer_ok": answer_ok,
        "keyword_total": len(expect_kw),
        "keyword_hit": len(kw_hit),
        "keyword_miss": kw_miss,
        "forbid_hit": forbid_hit,
        "citation_ok": citation_ok,
        "expect_citation": case.get("expect_citation", False),
        "citations": len(citations),
        "selfcheck_ok": selfcheck_ok,
        "pass": intent_ok and answer_ok and citation_ok and selfcheck_ok,
        "answer": answer,
        "trace": result.get("trace") or [],
        "evidence": result.get("evidence") or {},
    }


def classify_action(result: dict) -> str:
    """把一次执行的产物归类为可观察动作，用于轨迹前缀回归。

    只看产物（answer + evidence），不看 Agent 的推理过程——这正是
    "提议者—审核者"里审核者只看产物、不与产出方共享上下文的原则。
    """
    answer = result.get("answer", "")
    evidence = result.get("evidence") or {}
    low = answer.lower()
    sql = (evidence.get("sql") or "")

    # 判定优先级：先看 evidence 里的客观事实（是否真的执行了查询/触发了护栏），
    # 再回退到答案文本。绝不先扫答案信号词——结论里的"口径/矛盾"等词会误判。
    # 1) 护栏触发：这是最明确的动作，evidence 直接记录
    if evidence.get("unsupported_window"):
        return "refuse_with_scope"
    if evidence.get("blocked_by_guard"):
        return "blocked_by_guard"
    # 2) 有 SQL 且执行成功：按 SQL 结构分类（客观证据）
    if sql:
        if "metrics" in sql.lower() or "m.month" in sql.lower():
            return "query_monthly_metrics"
        # return_total_spend 专指"用户没要全库合计却返回了全库合计"这种错误动作。
        # 是否有此错误由 refuse 验证器的 forbid_substrings 负责，动作分类只描述
        # "执行了 SQL 汇总查询"这一客观事实，交由场景的 acceptable/forbidden 判定。
        return "query_with_template"
    # 3) 无 SQL：才回退到答案文本里的拒绝信号（如 SQL 生成失败时的兜底）
    if any(s in low for s in ("不支持", "没法转成", "无法查询", "暂时没法", "不能转")):
        return "refuse_with_scope"
    return "other"


def verify_prefix(case: dict, result: dict) -> dict:
    """轨迹前缀回归验证：实际动作须落在可接受集合、且不触发禁止动作。"""
    action = classify_action(result)
    acceptable = case.get("acceptable_actions", [])
    forbidden = case.get("forbidden_actions", [])
    answer = result.get("answer", "")

    # 禁止子串一票否决：即使动作类别"看起来合法"，答案里出现被禁止的编造数字
    # 也算失败。这是"验证器核实客观事实"的直接应用。
    forbid_hit = [b for b in case.get("forbid_substrings", []) if b in answer]
    passed = (
        not forbid_hit
        and action not in forbidden
        and (not acceptable or action in acceptable)
    )
    return {
        "id": case["id"],
        "scenario": case.get("scenario", ""),
        "action": action,
        "acceptable": acceptable,
        "forbidden": forbidden,
        "forbid_hit": forbid_hit,
        "pass": passed,
        "answer": answer,
        "evidence": result.get("evidence") or {},
    }
