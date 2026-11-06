"""商机智能辅助 Agent。

两条路径：
- 指定客户 -> 单客户商机建议（打点：下一步动作 + 话术 + 预估增量 + 依据引用）
- 不指定客户 -> 批量商机名单（打点：几百个中小客户里今天该先打给谁）

规则引擎负责打分，知识库负责"怎么说"，模型负责把两者拼成人能直接用的建议。
"""

from __future__ import annotations

from typing import Any

from ..context import Context
from ..state import AgentState
from ..tools import scoring
from ..tools.rag import format_context
from .profile import build_evidence, layer_of

TALK_TRACK = {
    "扩量增投": "您近 30 天 ROAS {roas:.1f}，预算已经跑到 {util}%，再往上加 25% 左右是能承接住的；"
                 "我建议先把搜索的日预算提上去，新开一条到店计划，一周后我们一起看复盘。",
    "续费": "这个周期您的线索成本是 {cpa:.0f} 元，比同行业低，套餐 {days} 天后到期，"
            "我按现在效果给您做了续费方案，标准版维持不变，另外送两组定制素材。",
    "升级套餐": "您现在月消耗已经到 {spend:,.0f} 元，基础版没有专属优化师和素材支持，"
                "升级标准版后每月 4 组素材 + 双周复盘，对转化率提升更直接。",
    "流失挽留": "先不聊预算，您之前提到的{issue}问题我复盘了一遍，找到了两个具体原因，"
                "这周给您一版优化方案，先跑两周看效果，不行我们再谈别的。",
    "效果优化": "CVR 从 {cvr_prev:.2f}% 掉到 {cvr_cur:.2f}%，主要问题在素材老化和落地页表单，"
                "这周我先补 3 组新素材，表单字段可以砍到 3 个试试。",
    "激活投放": "您这个月预算只用了 {util}%，不是效果问题，是投不出去；"
                "我先帮您把出价和定向调一版，把量跑起来再谈增投。",
}


def _cpa(adv: dict) -> float:
    conv = float(adv["conversions_30d"] or 0)
    spend = float(adv["spend_30d"] or 0)
    return spend / conv if conv else 0.0


def _cvr_delta(ev: dict) -> tuple[float, float]:
    metrics = ev["metrics"]
    if len(metrics) >= 2:
        return float(metrics[-2]["cvr"]) * 100, float(metrics[-1]["cvr"]) * 100
    return float(ev["advertiser"]["cvr"]) * 100, float(ev["advertiser"]["cvr"]) * 100


def _issue_of(ev: dict) -> str:
    """把内部信号翻译成客户能听懂的业务语言。"""
    codes = [s["code"] for s in ev["signals"]]
    if "CVR_DROP" in codes:
        return "线索质量"
    if "SPEND_DROP" in codes:
        return "消耗掉量"
    if "NEG_SENTIMENT" in codes:
        return "投放效果"
    return "投放节奏"


def talk_track(opp: dict, ev: dict) -> str:
    adv = ev["advertiser"]
    cvr_prev, cvr_cur = _cvr_delta(ev)
    tpl = TALK_TRACK.get(opp["type"])
    if not tpl:
        return ""
    return tpl.format(
        roas=float(adv["roas"]), util=ev["utilization"], cpa=_cpa(adv),
        days=scoring.days_until(adv["plan_expire_date"]), spend=float(adv["spend_30d"]),
        issue=_issue_of(ev), cvr_prev=cvr_prev, cvr_cur=cvr_cur,
    )


# --------------------------------------------------------------------------
# 检索：用行业 + 商机类型 + 风险标签拼查询词，命中案例与策略
# --------------------------------------------------------------------------
def retrieve_support(ctx: Context, ev: dict, opp: dict) -> list:
    adv = ev["advertiser"]
    query = " ".join([
        adv["industry"], opp["type"], " ".join(s["label"] for s in ev["signals"]),
        adv["main_objective"],
    ])
    hits = ctx.kb.retrieve(query, top_k=ctx.settings.top_k)
    return hits


def single_opportunity(ctx: Context, advertiser_id: str) -> dict[str, Any] | None:
    ev = build_evidence(ctx, advertiser_id)
    if not ev:
        return None
    if not ev["opportunities"]:
        return {"ev": ev, "opp": None, "hits": []}
    opp = ev["opportunities"][0]
    return {"ev": ev, "opp": opp, "hits": retrieve_support(ctx, ev, opp)}


def batch_opportunities(ctx: Context, top_n: int = 5) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for adv in ctx.store.list_advertisers(limit=200):
        ev = build_evidence(ctx, adv["advertiser_id"])
        if not ev or not ev["opportunities"]:
            continue
        opp = ev["opportunities"][0]
        rows.append({
            "advertiser_id": adv["advertiser_id"],
            "name": adv["name"],
            "industry": adv["industry"],
            "layer": layer_of(float(adv["spend_30d"])),
            "type": opp["type"],
            "score": opp["score"],
            "reason": opp["reason"],
            "uplift": opp["expected_uplift"],
            "confidence": opp["confidence"],
            "risk_level": ev["risk_level"],
        })
    rows.sort(key=lambda r: (r["score"], r["uplift"]), reverse=True)
    return rows[:top_n]


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------
def render_single(ev: dict, opp: dict, hits: list) -> str:
    adv = ev["advertiser"]
    lines = [
        f"### 商机建议 · {adv['name']}（{adv['advertiser_id']}）",
        "",
        f"**推荐动作**：{opp['type']}（{opp['score']} 分，置信度 {opp['confidence']}）",
        "",
        "**为什么是它**",
        f"- {opp['reason']}",
        f"- 近 30 天消耗 {float(adv['spend_30d']):,.0f} 元，预算利用率 {ev['utilization']}%，ROAS {float(adv['roas']):.1f}",
        f"- 风险等级：{ev['risk_level']}，命中信号：" + ("、".join(s["label"] for s in ev["signals"]) or "无"),
        "",
        "**怎么做**",
    ]
    for i, step in enumerate(_steps(opp["type"], ev), 1):
        lines.append(f"{i}. {step}")
    lines += [
        "",
        "**开口话术**",
        f"> {talk_track(opp, ev)}",
        "",
        f"**预估增量**：约 {opp['expected_uplift']:,.0f} 元/月（按历史同类客户保守估算，不承诺结果）",
    ]
    if ev["signals"]:
        lines += ["", "**风险提示**：" + "；".join(s["detail"] for s in ev["signals"][:2])]

    if hits:
        lines += ["", "**参考依据**"]
        for i, hit in enumerate(hits[:3], 1):
            lines.append(f"- [{i}]《{hit.chunk.source}》- {hit.chunk.title}")
    return "\n".join(lines)


def _steps(otype: str, ev: dict) -> list[str]:
    s = {
        "扩量增投": [
            "先确认承接能力（门店/客服/发货），避免扩量后转化率被稀释",
            "按 20%~30% 分两档提预算，优先提搜索，再新开到店类计划",
            "约定 7 天复盘，用 CPA 与转化数两个指标判断是否继续放量",
        ],
        "续费": [
            "到期前 21 天发本周期复盘，用正向数据铺路",
            "带两档续费方案（原套餐 / 升级）上门或电话沟通",
            "到期前 7 天确认决策链，避免对接人变更导致断档",
        ],
        "升级套餐": [
            "整理本月素材需求清单，说明标准版能补上哪几项",
            "用同行业升级客户的效果对比做参照，量化增值",
            "升级与年度签约打包，避免单点涨价引起抵触",
        ],
        "流失挽留": [
            "48 小时内电话沟通，先解决最痛的一个具体问题",
            "准备三套方案：优化 / 降配 / 暂停保留账号",
            "设定两周观察期与明确止损线，给客户确定性",
        ],
        "效果优化": [
            "先拆渠道定位问题，再拆素材老化，最后查落地页表单",
            "本周补 3 组新素材，表单字段精简到 3 个以内",
            "两周后对比 CVR，决定是否恢复原有预算",
        ],
        "激活投放": [
            "检查出价是否高于行业均值导致曝光不足",
            "放宽定向：地域从单区扩到核心商圈，减少兴趣标签堆叠",
            "素材补到 5 组以上再放量，否则消耗起不来",
        ],
    }
    return s.get(otype, ["按标准复盘动作执行：拉数据 → 定位问题 → 给方案 → 约复盘"])


def render_batch(rows: list[dict]) -> str:
    lines = [
        "### 今日商机名单（按优先级）",
        "",
        "| # | 客户 | 层级 | 商机类型 | 分数 | 预估增量(元/月) | 主要依据 |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"| {i} | {r['name']}（{r['advertiser_id']}） | {r['layer']} | {r['type']} | "
            f"{r['score']} | {r['uplift']:,.0f} | {r['reason']} |"
        )
    lines += ["", "排序口径：商机分数优先，同分看预估增量；分数由规则引擎计算，可逐条追溯到指标。"]
    return "\n".join(lines)


def opportunity_node(state: AgentState, ctx: Context) -> dict:
    trace = list(state.get("trace", []))
    advertiser_id = state.get("advertiser_id")

    if not advertiser_id:
        rows = batch_opportunities(ctx)
        trace.append({"step": "opportunity", "detail": f"批量扫描 {len(rows)} 条高优先商机（全库 42 客户）"})
        return {
            "answer": render_batch(rows),
            "evidence": {"batch": rows},
            "citations": [],
            "trace": trace,
        }

    pack = single_opportunity(ctx, advertiser_id)
    if not pack:
        return {"answer": f"没有找到客户 {advertiser_id} 的数据。", "evidence": {},
                "citations": [], "trace": trace}

    ev, opp, hits = pack["ev"], pack["opp"], pack["hits"]
    trace.append({"step": "opportunity",
                  "detail": f"打分命中 {len(ev['opportunities'])} 条商机，检索 {len(hits)} 段策略/案例",
                  "advertiser_id": advertiser_id})

    citations = sorted({h.chunk.source for h in hits})
    answer = None
    if ctx.llm.available and opp:
        answer = ctx.llm.chat([
            {"role": "system", "content":
             "你是广告代理商的中小客户销售顾问。基于【客户数据】和【参考资料】给出下一步动作建议，要求："
             "1) 先给结论再给依据；2) 每个结论必须有数字；3) 给一条可以直接说出口的话术；"
             "4) 只能引用参考资料里出现过的策略，不要编造；5) 用 Markdown。"},
            {"role": "user", "content":
             f"【客户数据】\n{_ev_to_prompt(ev)}\n\n"
             f"【推荐商机】{opp['type']}（{opp['score']} 分）：{opp['reason']}\n"
             f"【预估增量】{opp['expected_uplift']:,.0f} 元/月\n\n"
             f"【参考资料】\n{format_context(hits)}\n\n"
             f"请输出：推荐动作 / 为什么 / 怎么做（3 步）/ 开口话术 / 风险提示。"},
        ], temperature=0.4, max_tokens=900)

    if not answer:
        answer = render_single(ev, opp, hits) if opp else (
            f"{ev['advertiser']['name']} 当前未命中任何商机规则，建议按常规复盘节奏跟进。"
        )
        trace.append({"step": "opportunity", "detail": "模型不可用，使用规则模板生成建议"})

    return {
        "answer": answer,
        "evidence": {
            "advertiser_id": advertiser_id,
            "name": ev["advertiser"]["name"],
            "risk_level": ev["risk_level"],
            "top_opportunity": {"type": opp["type"], "score": opp["score"],
                                "uplift": opp["expected_uplift"]} if opp else None,
            "retrieved": [h.as_dict() for h in hits],
        },
        "citations": citations,
        "trace": trace,
    }


def _ev_to_prompt(ev: dict) -> str:
    adv = ev["advertiser"]
    return (
        f"{adv['name']}（{adv['industry']}，{adv['region']}），{adv['plan']}，"
        f"月预算 {adv['monthly_budget']}，近30天消耗 {adv['spend_30d']}，"
        f"上期消耗 {adv['spend_prev_30d']}，利用率 {ev['utilization']}%，"
        f"ROAS {adv['roas']}，CVR {float(adv['cvr'])*100:.2f}%，转化 {adv['conversions_30d']} 条，"
        f"套餐到期 {adv['plan_expire_date']}，上次沟通 {adv['last_contact_date']}；"
        f"风险信号：{'；'.join(s['label']+'-'+s['detail'] for s in ev['signals']) or '无'}"
    )
