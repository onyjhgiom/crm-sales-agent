"""失败归因：从轨迹里定位首个错误，而不是把最后的报错当根因。

设计依据（ai-agent-book 第 7 章「失败归因：从整条轨迹定位首个错误」）：
- 归因对象是"首个导致任务偏离的错误"，后续错误往往只是连锁反应；
- 归因记录要结构化：错误类别 / 首错步骤 / 责任方 / 客观证据 / 根因 vs 后果；
- 区分根因（root cause）与后果（consequence），多个类别按"最早且能解释后续失败"选主因。

本项目是针对销售问答的轻量实现：验证结果里已经带了每一步 trace 与证据，
归因器扫描 trace，按预定义的错误签名定位首个偏离点。归因结果写入
evals/attributions.json，供回归与复盘使用。
"""

from __future__ import annotations

from typing import Any

# 错误签名：按"首个偏离"的顺序扫描。越靠前越高优先级。
# (类别, 责任方, 判定函数) —— 责任方用于避免把 Harness 问题记成模型能力问题。
_SIGNATURES = [
    (
        "口径边界未拦截",
        "harness/tool",
        lambda v: v["verifier"] == "refusal" and bool(v.get("forbid_hit")),
        "答案出现了被禁止的编造数字——自定义时间窗口没被拦截，模糊命中了汇总模板",
    ),
    (
        "口径边界识别但表达缺失",
        "harness/prompt",
        lambda v: v["verifier"] == "refusal" and not v["answer_ok"] and not v.get("forbid_hit"),
        "识别出了边界，但拒绝说明缺少必需信号词或口径解释",
    ),
    (
        "意图路由错误",
        "harness/router",
        lambda v: not v["intent_ok"],
        "问题被分到了错误的处理分支，后续检索与生成再好也无法命中",
    ),
    (
        "关键事实缺失",
        "harness/tool-or-llm",
        lambda v: not v["answer_ok"] and v.get("keyword_hit", 0) < v.get("keyword_total", 0),
        "答案缺少期望的客观事实（关键词命中不全）",
    ),
    (
        "引用缺失",
        "harness/rag",
        lambda v: not v["citation_ok"],
        "知识类回答未带出处，销售不敢转给客户",
    ),
    (
        "自检漏检",
        "harness/review",
        lambda v: not v["selfcheck_ok"],
        "反思节点判定通过但其他维度失败——自检本身有盲区",
    ),
]


def _first_error_step(v: dict) -> tuple[int | None, str]:
    """在 trace 里定位首个可疑步骤。返回 (步号, 步骤描述)。

    轨迹前缀回归的思路：找到"从这一步开始已经错了"，而非最后报错的那一步。
    """
    trace = v.get("trace") or []
    # 若答案里漏出了被禁止的数字，首个错误一定在 SQL 生成/执行阶段——
    # 模板"模糊命中"本身不会在 trace 里写"出错"，但那是偏离的起点。
    if v.get("forbid_hit"):
        for i, step in enumerate(trace):
            if "生成 SQL" in step.get("detail", ""):
                return i, f"{step.get('step')}: {step.get('detail')}（模糊命中错误模板，返回了被禁止的数字）"
    for i, step in enumerate(trace):
        detail = step.get("detail", "")
        # 这些是明确的"偏离信号"：安全拦截、口径边界、兜底模板、查询失败
        if any(sig in detail for sig in ("安全校验拦截", "口径边界", "执行失败", "兜底", "模型不可用")):
            return i, f"{step.get('step')}: {detail}"
    return None, ""


def attribute(verification: dict) -> dict | None:
    """对一条失败的验证结果做归因。通过则返回 None。"""
    if verification.get("pass"):
        return None

    category, owner, evidence_note = "未分类失败", "unknown", "未匹配到已知错误签名"
    for cat, who, predicate, note in _SIGNATURES:
        if predicate(verification):
            category, owner, evidence_note = cat, who, note
            break

    step_idx, step_detail = _first_error_step(verification)

    # 根因 vs 后果：把 trace 里第一个偏离步之后的变化记为后果
    consequences = []
    trace = verification.get("trace") or []
    if step_idx is not None and step_idx + 1 < len(trace):
        consequences = [f"{s.get('step')}: {s.get('detail')}" for s in trace[step_idx + 1:]][:3]

    return {
        "case_id": verification["id"],
        "question": verification["question"],
        "category": category,
        "owner": owner,
        "first_error_step": step_idx,
        "first_error_detail": step_detail,
        "root_cause": evidence_note,
        "consequences": consequences,
        "evidence": {
            "expect_intent": verification["expect_intent"],
            "got_intent": verification["got_intent"],
            "keyword_hit": verification["keyword_hit"],
            "keyword_total": verification["keyword_total"],
            "keyword_miss": verification["keyword_miss"],
            "forbid_hit": verification["forbid_hit"],
            "unsupported_window": (verification.get("evidence") or {}).get("unsupported_window"),
        },
        "trace": trace,
    }
