"""业务规则引擎：流失风险信号 + 商机打分。

为什么把这部分做成纯规则，而不是交给大模型判断：
- 风险信号是**可审计**的，"哪条规则命中"必须能追溯到具体数字，
  否则销售不敢按这个名单打电话；
- 大模型擅长表达与归纳，不适合做阈值判断，这里让规则负责"算"，
  让模型负责"说"，职责分离也更容易做评估。

评分口径全部来自 data/knowledge/playbooks 里的策略文档，改策略就改这里的常量。
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

from .. import config

TODAY = dt.date.today()

# 阈值集中放在 config/rules.json：业务方调口径不用改代码、不用重新发版。
# 这里保留同名的模块常量，是为了让其它模块继续按 `scoring.EXPIRING_DAYS` 读，
# 也方便在测试里直接引用。
DEFAULTS: dict[str, float] = {
    "spend_drop_threshold": 0.20,
    "cvr_drop_threshold": 0.15,
    "no_contact_days": 21,
    "low_utilization": 0.60,
    "full_utilization": 0.85,
    "expiring_days": 30,
    "roas_good": 2.0,
    "upgrade_spend_threshold": 30000,
    "negative_sentiment_window": 3,
    "negative_sentiment_threshold": 2,
    "layer_s_threshold": 60000,
    "layer_a_threshold": 30000,
    "layer_b_threshold": 15000,
}
RATIO_KEYS = {"spend_drop_threshold", "cvr_drop_threshold", "low_utilization", "full_utilization"}


def load_rules(path: str | None = None) -> dict[str, float]:
    """读配置文件；文件缺失/写坏/取值非法时逐项回落到默认值，不让服务起不来。"""
    path = path or os.getenv("CRM_RULES_PATH") or os.path.join(config.ROOT, "config", "rules.json")
    rules = dict(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return rules
    for key, default in DEFAULTS.items():
        value = raw.get(key) if isinstance(raw, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            continue
        if key in RATIO_KEYS and value > 1:  # 比率类填成 20 而不是 0.2，属于填错
            continue
        rules[key] = float(value) if isinstance(default, float) else int(value)
    return rules


RULES = load_rules()

SPEND_DROP_THRESHOLD = RULES["spend_drop_threshold"]
CVR_DROP_THRESHOLD = RULES["cvr_drop_threshold"]
NO_CONTACT_DAYS = int(RULES["no_contact_days"])
LOW_UTILIZATION = RULES["low_utilization"]
FULL_UTILIZATION = RULES["full_utilization"]
EXPIRING_DAYS = int(RULES["expiring_days"])
ROAS_GOOD = RULES["roas_good"]
UPGRADE_SPEND_THRESHOLD = RULES["upgrade_spend_threshold"]


def days_since(date_str: str) -> int:
    try:
        d = dt.date.fromisoformat(str(date_str)[:10])
    except ValueError:
        return 999
    return (TODAY - d).days


def days_until(date_str: str) -> int:
    try:
        d = dt.date.fromisoformat(str(date_str)[:10])
    except ValueError:
        return 999
    return (d - TODAY).days


# --------------------------------------------------------------------------
# 流失风险
# --------------------------------------------------------------------------
def risk_signals(adv: dict, campaigns: list[dict], metrics: list[dict],
                 interactions: list[dict]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []

    spend_30d = float(adv["spend_30d"] or 0)
    spend_prev = float(adv["spend_prev_30d"] or 0)
    if spend_prev > 0:
        drop = (spend_prev - spend_30d) / spend_prev
        if drop >= SPEND_DROP_THRESHOLD:
            signals.append({
                "code": "SPEND_DROP",
                "label": "消耗明显下滑",
                "detail": f"近 30 天消耗 {spend_30d:,.0f} 元，环比下降 {drop * 100:.0f}%",
            })

    if len(metrics) >= 2:
        prev_cvr = float(metrics[-2]["cvr"] or 0)
        cur_cvr = float(metrics[-1]["cvr"] or 0)
        if prev_cvr > 0:
            cvr_drop = (prev_cvr - cur_cvr) / prev_cvr
            if cvr_drop >= CVR_DROP_THRESHOLD:
                signals.append({
                    "code": "CVR_DROP",
                    "label": "转化率走低",
                    "detail": f"CVR 由 {prev_cvr * 100:.2f}% 降至 {cur_cvr * 100:.2f}%，下降 {cvr_drop * 100:.0f}%",
                })

    gap = days_since(adv["last_contact_date"])
    if gap > NO_CONTACT_DAYS:
        signals.append({
            "code": "NO_CONTACT",
            "label": "跟进断档",
            "detail": f"距上次有效沟通已 {gap} 天（阈值 {NO_CONTACT_DAYS} 天）",
        })

    window = int(RULES["negative_sentiment_window"])
    threshold = int(RULES["negative_sentiment_threshold"])
    neg = [i for i in interactions[:window] if i["sentiment"] == "负面"]
    if len(neg) >= threshold:
        signals.append({
            "code": "NEG_SENTIMENT",
            "label": "负面情绪累积",
            "detail": f"最近 {window} 次沟通中有 {len(neg)} 次负面反馈，且未看到闭环动作",
        })

    expire_in = days_until(adv["plan_expire_date"])
    if 0 <= expire_in <= EXPIRING_DAYS:
        signals.append({
            "code": "EXPIRING",
            "label": "套餐临近到期",
            "detail": f"距套餐到期还有 {expire_in} 天，已进入续费窗口",
        })

    return signals


def risk_level(signals: list[dict]) -> str:
    n = len(signals)
    if n >= 3:
        return "极高风险"
    if n == 2:
        return "高风险"
    if n == 1:
        return "中风险"
    return "低风险"


def health_score(adv: dict, signals: list[dict]) -> int:
    """100 分制健康度，扣分项全部可解释。"""
    score = 100
    penalties = {
        "SPEND_DROP": 20,
        "CVR_DROP": 15,
        "NO_CONTACT": 15,
        "NEG_SENTIMENT": 15,
        "EXPIRING": 5,
    }
    for s in signals:
        score -= penalties.get(s["code"], 5)
    util = utilization(adv)
    if util < LOW_UTILIZATION:
        score -= 10
    if float(adv["roas"] or 0) < 1.2:
        score -= 10
    return max(0, min(100, score))


def utilization(adv: dict) -> float:
    budget = float(adv["monthly_budget"] or 0)
    if budget <= 0:
        return 0.0
    return min(2.0, float(adv["spend_30d"] or 0) / budget)


# --------------------------------------------------------------------------
# 商机
# --------------------------------------------------------------------------
OPPORTUNITY_RULES = [
    # (类型, 判定函数, 基础分, 说明模板)
    # 流失挽留必须由"实质性"风险触发：仅断档或仅临近到期，客户其实还在正常合作，
    # 这时候推挽留方案会显得销售不了解客户
    ("流失挽留", lambda a, c, m, i, s: _risk_heavy(s), 72, "命中 {n} 条实质性风险信号，先解决最痛的问题再谈钱"),
    ("续费", lambda a, c, m, i, s: 0 <= days_until(a["plan_expire_date"]) <= EXPIRING_DAYS, 65, "套餐 {days} 天后到期，已进入续费窗口"),
    ("扩量增投", lambda a, c, m, i, s: float(a["roas"] or 0) >= ROAS_GOOD and utilization(a) >= FULL_UTILIZATION, 68, "ROAS {roas:.1f} 且预算利用率 {util:.0%}，预算已成瓶颈"),
    ("升级套餐", lambda a, c, m, i, s: a["plan"] == "基础版" and float(a["spend_30d"]) >= UPGRADE_SPEND_THRESHOLD, 60, "基础版客户月消耗已达 {spend:,.0f} 元，需要素材与复盘支持"),
    ("效果优化", lambda a, c, m, i, s: any(x["code"] == "CVR_DROP" for x in s), 55, "转化率下滑，优先排查素材与落地页"),
    ("激活投放", lambda a, c, m, i, s: utilization(a) < LOW_UTILIZATION, 50, "预算利用率仅 {util:.0%}，先解决投不出去"),
]

SUBSTANTIVE_SIGNALS = {"SPEND_DROP", "CVR_DROP", "NEG_SENTIMENT"}


def _risk_heavy(signals: list[dict]) -> bool:
    """只有"消耗掉、转化掉、客户不满"这类实质问题才算流失风险。"""
    substantive = [s for s in signals if s["code"] in SUBSTANTIVE_SIGNALS]
    if len(substantive) >= 2:
        return True
    return len(substantive) == 1 and substantive[0]["code"] == "NEG_SENTIMENT"


def score_opportunities(adv: dict, campaigns: list[dict], metrics: list[dict],
                        interactions: list[dict], signals: list[dict]) -> list[dict[str, Any]]:
    util = utilization(adv)
    roas = float(adv["roas"] or 0)
    spend = float(adv["spend_30d"] or 0)
    out: list[dict[str, Any]] = []

    for name, cond, base, tpl in OPPORTUNITY_RULES:
        try:
            hit = cond(adv, campaigns, metrics, interactions, signals)
        except Exception:  # noqa: BLE001 - 单条规则出错不该影响其它商机
            hit = False
        if not hit:
            continue
        score = base
        # 效果越好、体量越大，分越高；风险信号越多，减分
        score += int(min(15, max(0, (roas - ROAS_GOOD) * 8)))
        score += int(min(12, spend / 10000 * 3))
        score -= len(signals) * 4 if name != "流失挽留" else 0
        score = max(0, min(100, score))
        out.append({
            "type": name,
            "score": score,
            "reason": tpl.format(
                n=len(signals), days=days_until(adv["plan_expire_date"]),
                roas=roas, util=util, spend=spend,
            ),
            "expected_uplift": _uplift(name, adv, util),
            "confidence": _confidence(name, roas, signals),
        })

    # 业务优先级优先于分数：先保命（挽留）→ 再续约 → 再增量。
    # 只按分数排会出现"客户下周就到期了却建议他先加预算"这种反常识结论。
    out.sort(key=lambda x: (PRIORITY.get(x["type"], 9), -x["score"]))
    return out


PRIORITY = {
    "流失挽留": 0,
    "续费": 1,
    "扩量增投": 2,
    "升级套餐": 3,
    "效果优化": 4,
    "激活投放": 5,
}


def _uplift(name: str, adv: dict, util: float) -> float:
    """粗略估算增量消耗（元/月），给销售一个谈判锚点，不承诺结果。"""
    spend = float(adv["spend_30d"] or 0)
    if name == "扩量增投":
        return round(spend * 0.25, -2)
    if name == "升级套餐":
        return round(min(4000, spend * 0.08), -2)
    if name == "激活投放":
        return round(max(0, float(adv["monthly_budget"] or 0) - spend), -2)
    if name == "流失挽留":
        return round(spend * 0.3, -2)
    return 0.0


def _confidence(name: str, roas: float, signals: list[dict]) -> str:
    if name == "流失挽留":
        return "高" if len(signals) >= 3 else "中"
    if name == "扩量增投":
        return "高" if roas >= 2.5 else "中"
    return "中"
