"""路由节点：判断这一句话该交给哪个子 Agent。

策略是"规则优先、模型兜底"：
- 销售在真实场景里 80% 的问法是固定的几类，关键词规则又快又稳；
- 只有规则完全没命中时，才花一次模型调用去做分类，省钱也省延迟；
- 分类结果写进 trace，前端可以把"为什么走到这个 Agent"展示出来。
"""

from __future__ import annotations

import re

from ..context import Context
from ..state import AgentState

PROFILE_WORDS = ("画像", "客户情况", "这家", "了解一下", "档案", "概览", "什么情况")
OPPORTUNITY_WORDS = ("商机", "增投", "续费", "升级", "流失", "挽留", "怎么跟进", "话术",
                     "建议", "下一步", "动作", "机会", "该不该")
BATCH_WORDS = ("名单", "今天该", "优先跟进", "批量", "先打给谁", "排个序", "优先联系",
               "先打", "该打", "打电话")
ANALYSIS_WORDS = ("多少", "统计", "排名", "环比", "汇总", "下降", "下滑", "分布",
                  "占比", "top", "最高", "最低", "合计", "增长率", "roas", "cvr", "转化率",
                  "到期", "负面", "投诉", "不满")
KNOWLEDGE_WORDS = ("sop", "策略", "规则", "口径", "判断标准", "标准动作", "经验", "方法",
                   "预警", "信号", "红线", "报价")
# "怎么做"类问法优先走知识库：销售问"怎么谈续费"时并不想要一份名单
HOWTO_WORDS = ("怎么", "如何", "为什么", "什么情况下", "什么时候", "原则", "技巧", "应该")
# 比较类问法（环比 / 跨客户对比）：这类问题必须出两边数字，不能当商机建议回答
COMPARE_MONTH_RE = re.compile(r"(比上个月|比上月|环比|同比|好还是差|好坏|走势|趋势)")
COMPARE_PAIR_RE = re.compile(r"[Aa]\d{3}\s*(和|跟|与|vs|VS|、)\s*[Aa]\d{3}")


def is_compare(text: str) -> bool:
    """只有"能算出对比"的问法才算比较类：环比 / 两个客户做对比。"""
    return bool(COMPARE_MONTH_RE.search(text) or COMPARE_PAIR_RE.search(text))


def _match(text: str, words: tuple[str, ...]) -> int:
    return sum(1 for w in words if w.lower() in text.lower())


def resolve_advertiser(state: AgentState, ctx: Context) -> str | None:
    """按 显式 ID > 客户名片段 > 上一轮提及 的顺序定位客户。"""
    text = state.get("question", "")
    if state.get("advertiser_id"):
        return state["advertiser_id"]

    m = re.search(r"[Aa]\d{3}", text)
    if m:
        return m.group(0).upper()

    for adv in ctx.store.list_advertisers(limit=42):
        # 客户名通常 4~6 字，取前 3 字做匹配，容忍口语省略
        if adv["name"][:3] in text:
            return adv["advertiser_id"]

    return None


def decide_intent(text: str, advertiser_id: str | None) -> tuple[str, str]:
    """把意图判定抽成纯函数，方便单测和评估里复用同一套口径。"""
    profile_hit = _match(text, PROFILE_WORDS)
    opp_hit = _match(text, OPPORTUNITY_WORDS)
    ana_hit = _match(text, ANALYSIS_WORDS)
    kn_hit = _match(text, KNOWLEDGE_WORDS)
    howto = _match(text, HOWTO_WORDS)
    batch = _match(text, BATCH_WORDS)

    # 1) 点名了客户：画像 / 商机 / 分析三选一，客户级问题优先于通用问法
    if advertiser_id:
        # 比较类要先判：否则"比上个月好还是差"会被商机词截走
        if is_compare(text):
            return "analysis", "比较类问题（环比/跨客户），需要出两边的数字"
        if ana_hit and ana_hit > profile_hit and ana_hit > opp_hit:
            return "analysis", f"客户已定位，且命中 {ana_hit} 个分析词"
        if opp_hit > profile_hit and opp_hit > 0:
            return "opportunity", f"客户已定位且命中 {opp_hit} 个商机词"
        if profile_hit:
            return "profile", f"客户已定位且命中 {profile_hit} 个画像词"
        if howto or kn_hit:
            return "knowledge", f"客户已定位，但问题是方法论（{howto + kn_hit} 个词）"
        return "opportunity", "客户已定位，默认给商机建议"

    # 2) 方法论问题：没有点名客户时，"怎么谈/什么情况下能"这类问题查知识库
    if howto or kn_hit:
        return "knowledge", f"命中了 {howto + kn_hit} 个方法论词"

    # 3) 批量名单
    if batch:
        return "opportunity", f"命中 {batch} 个批量词，走批量商机扫描"

    # 4) 数据统计
    if ana_hit:
        return "analysis", f"命中 {ana_hit} 个分析词"

    # 5) 画像 / 商机
    if opp_hit:
        return "opportunity", f"命中 {opp_hit} 个商机词"
    if profile_hit:
        return "profile", f"命中 {profile_hit} 个画像词"

    return "smalltalk", "规则未命中"


def route(state: AgentState, ctx: Context) -> dict:
    text = state.get("question", "")
    advertiser_id = resolve_advertiser(state, ctx)
    intent, reason = decide_intent(text, advertiser_id)

    if intent == "smalltalk" and ctx.llm.available:
        llm_intent = _llm_classify(text, ctx)
        if llm_intent:
            intent = llm_intent
            reason = "规则未命中，由模型分类"

    if advertiser_id and intent == "analysis":
        # 带客户名的统计问题仍然走分析，但要把客户带过去做筛选
        reason += "（限定到该客户）"

    trace = list(state.get("trace", []))
    trace.append({"step": "router", "detail": f"意图={intent}（{reason}）",
                  "intent": intent, "advertiser_id": advertiser_id})

    return {"intent": intent, "advertiser_id": advertiser_id, "trace": trace}


def _llm_classify(text: str, ctx: Context) -> str | None:
    if not ctx.llm.available:
        return None
    out = ctx.llm.chat_json([
        {"role": "system", "content": "把销售的问题分类，只输出 JSON：{\"intent\": \"profile|opportunity|analysis|knowledge|smalltalk\"}"},
        {"role": "user", "content": text},
    ])
    if not out:
        return None
    intent = str(out.get("intent", "")).strip()
    return intent if intent in {"profile", "opportunity", "analysis", "knowledge", "smalltalk"} else None
