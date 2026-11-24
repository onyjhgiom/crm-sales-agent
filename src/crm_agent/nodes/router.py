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
from ..memory import build_history_prompt
from ..planning import slots_prompt

_INTENT_VALUES = {"profile", "opportunity", "analysis", "knowledge", "smalltalk"}

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

# 指代 / 延续：销售追问时经常不说客户名，只说"他""这家""继续""还有呢"。
# 排除"其他/它们"这类复数指代，避免把批量问题错误地绑定到单个客户。
ANAPHORA_RE = re.compile(
    r"(?<![其])他(?!们)|(?<![其])它(?!们)|她(?!们)|这家|这个客户|该客户|这家公司|"
    r"对方|上面那个|刚才那个|这人|那人"
)
CONTINUATION_RE = re.compile(
    r"^\s*(继续(说|吧|呢)?|接着说|再说(吧|呢)?|还有呢|还有吗|然后呢|那呢|"
    r"详细说|展开说|具体点|具体说说|呢|嗯)\s*[？?。!！]*$"
)
SHORT_FOLLOWUP_RE = re.compile(r"^\s*(那|那么|接着|然后|再).{0,18}[呢吗？?]\s*$")


def is_compare(text: str) -> bool:
    """只有"能算出对比"的问法才算比较类：环比 / 两个客户做对比。"""
    return bool(COMPARE_MONTH_RE.search(text) or COMPARE_PAIR_RE.search(text))


def _match(text: str, words: tuple[str, ...]) -> int:
    return sum(1 for w in words if w.lower() in text.lower())


def _last_context(history: list[dict]) -> tuple[str | None, str | None]:
    """从历史里取最近一次的客户 ID 和意图（只认 assistant 轮，避免把中途提问当事实）。"""
    for turn in reversed(history or []):
        if turn.get("role") == "assistant":
            return turn.get("advertiser_id"), turn.get("intent")
    return None, None


def _is_followup(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    return bool(ANAPHORA_RE.search(stripped) or CONTINUATION_RE.match(stripped)
                or SHORT_FOLLOWUP_RE.match(stripped))


def resolve_advertiser(state: AgentState, ctx: Context) -> str | None:
    """按 显式 ID > 文本里 ID/客户名 > 上一轮提及 的顺序定位客户。

    上一轮提及只在"追问"时生效：问题里出现指代词（他/这家）、延续语（继续/还有呢），
    或"那…呢"这类短追问。否则像"转化率最低的 10 个客户"这种批量统计，
    即使上一轮聊过 A003 也不该被绑定到 A003。
    """
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

    history = state.get("history") or []
    if history and _is_followup(text):
        last_id, _ = _last_context(history)
        if last_id:
            return last_id

    return None


def has_intent_signal(text: str) -> bool:
    """问题里是否带有能独立判意图的关键词（不含指代词）。"""
    return any(_match(text, words) for words in
               (PROFILE_WORDS, OPPORTUNITY_WORDS, ANALYSIS_WORDS,
                KNOWLEDGE_WORDS, HOWTO_WORDS, BATCH_WORDS)) or is_compare(text)


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
    history = state.get("history") or []
    advertiser_id = resolve_advertiser(state, ctx)
    last_id, last_intent = _last_context(history)

    # 追问没有新意图词时沿用上一轮的意图。
    # 必须同时满足"历史非空 + 是追问 + 无意图词"，否则评估基线（无历史）完全不受影响。
    if (last_intent and last_intent in _INTENT_VALUES and history
            and _is_followup(text) and not has_intent_signal(text)):
        intent, reason = last_intent, f"追问沿用上一轮意图（{last_intent}）"
    else:
        intent, reason = decide_intent(text, advertiser_id)

    if intent == "smalltalk" and ctx.llm.available:
        llm_intent = _llm_classify(text, ctx, history=history)
        if llm_intent:
            intent = llm_intent
            reason = "规则未命中，由模型分类"

    if advertiser_id and intent == "analysis":
        # 带客户名的统计问题仍然走分析，但要把客户带过去做筛选
        reason += "（限定到该客户）"

    trace = list(state.get("trace", []))
    if history and _is_followup(text):
        if advertiser_id == last_id and last_id:
            reason += f"；客户沿用上一轮（{last_id}）"
        trace.append({"step": "context", "detail": f"读取对话记忆 {len(history)} 条，识别为追问"})
    trace.append({"step": "router", "detail": f"意图={intent}（{reason}）",
                  "intent": intent, "advertiser_id": advertiser_id})

    # 规划层：单意图产出的 plan 不改变下游行为；复合意图才会切到编排节点。
    planner = getattr(ctx, "planner", None)
    out = {"intent": intent, "advertiser_id": advertiser_id, "trace": trace}
    if planner is not None:
        plan = planner.plan(text, advertiser_id, history, ctx, intent=intent, reason=reason)
        out["plan"] = plan.to_dict()
        if plan.mode == "composite":
            trace.append({"step": "plan", "detail":
                          f"拆分为 {len(plan.tasks)} 个子任务："
                          + "、".join(t.intent for t in plan.tasks)})
    return out


def _llm_classify(text: str, ctx: Context, history: list[dict] | None = None) -> str | None:
    if not ctx.llm.available:
        return None
    prompt = text
    if history:
        # 在线模式下给分类模型一点对话背景，否则"那转化率呢"看不出在问什么。
        # 结构化槽位（客户/指标/时间口径）比纯原文更不容易被长对话淹没。
        from ..planning import extract_slots
        bg = build_history_prompt(history)
        slots_line = slots_prompt(extract_slots(history))
        context_block = "\n".join(x for x in (bg, slots_line) if x)
        if context_block:
            prompt = f"【对话背景】\n{context_block}\n\n【本轮问题】{text}"
    out = ctx.llm.chat_json([
        {"role": "system", "content": "把销售的问题分类，只输出 JSON：{\"intent\": \"profile|opportunity|analysis|knowledge|smalltalk\"}"},
        {"role": "user", "content": prompt},
    ])
    if not out:
        return None
    intent = str(out.get("intent", "")).strip()
    return intent if intent in {"profile", "opportunity", "analysis", "knowledge", "smalltalk"} else None
