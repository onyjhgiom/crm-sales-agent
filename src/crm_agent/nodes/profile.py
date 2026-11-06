"""客户画像 Agent。

画像不是把数据库字段念一遍，而是回答一个问题：**这个客户值不值得投入，下一步该做什么**。
所以输出的结构固定为"一句话判断 → 经营概况 → 趋势 → 风险 → 建议"，
其中"一句话判断"是销售在早会上能直接复述的那句话。
"""

from __future__ import annotations

from typing import Any

from ..context import Context
from ..state import AgentState
from ..tools import scoring

LAYER = [(scoring.RULES["layer_s_threshold"], "S"),
         (scoring.RULES["layer_a_threshold"], "A"),
         (scoring.RULES["layer_b_threshold"], "B"),
         (0, "C")]


def layer_of(spend: float) -> str:
    for threshold, name in LAYER:
        if spend >= threshold:
            return name
    return "C"


def build_evidence(ctx: Context, advertiser_id: str) -> dict[str, Any] | None:
    adv = ctx.store.get_advertiser(advertiser_id)
    if not adv:
        return None
    campaigns = ctx.store.get_campaigns(advertiser_id)
    metrics = ctx.store.get_metrics(advertiser_id)
    interactions = ctx.store.get_interactions(advertiser_id, limit=5)

    signals = scoring.risk_signals(adv, campaigns, metrics, interactions)
    return {
        "advertiser": adv,
        "layer": layer_of(float(adv["spend_30d"])),
        "utilization": round(scoring.utilization(adv) * 100, 1),
        "campaigns": campaigns,
        "metrics": metrics,
        "interactions": interactions,
        "signals": signals,
        "risk_level": scoring.risk_level(signals),
        "health": scoring.health_score(adv, signals),
        "opportunities": scoring.score_opportunities(adv, campaigns, metrics, interactions, signals),
    }


def _fmt_money(x: float) -> str:
    return f"{x:,.0f}"


def render_template(ev: dict[str, Any]) -> str:
    adv = ev["advertiser"]
    layer, util = ev["layer"], ev["utilization"]
    roas, cvr = float(adv["roas"]), float(adv["cvr"]) * 100
    spend, prev = float(adv["spend_30d"]), float(adv["spend_prev_30d"])
    delta = (spend - prev) / prev * 100 if prev else 0.0
    trend = "上升" if delta > 5 else ("下滑" if delta < -5 else "基本持平")

    top_opp = ev["opportunities"][0] if ev["opportunities"] else None
    verdict = _verdict(top_opp, ev) if top_opp else "数据平稳，暂无明确商机"
    sentence = (
        f"{adv['name']}（{layer} 层，{ev['risk_level']}）{verdict}："
        f"近 30 天消耗 {_fmt_money(spend)} 元、ROAS {roas:.1f}、CVR {cvr:.2f}%。"
    )

    lines = [
        f"### 客户画像 · {adv['name']}（{adv['advertiser_id']}）",
        "",
        f"**一句话判断**：{sentence}",
        "",
        "**经营概况**",
        f"- 行业 / 地区：{adv['industry']} / {adv['region']}，员工约 {adv['employee_scale']} 人",
        f"- 套餐：{adv['plan']}（{adv['plan_expire_date']} 到期，剩余 {scoring.days_until(adv['plan_expire_date'])} 天）",
        f"- 月预算 {_fmt_money(float(adv['monthly_budget']))} 元，近 30 天消耗 {_fmt_money(spend)} 元，预算利用率 {util}%",
        f"- 转化：CVR {cvr:.2f}%，CPC {float(adv['cpc']):.2f} 元，近 30 天转化 {adv['conversions_30d']} 条",
        f"- 效果：ROAS {roas:.1f}，健康度 {ev['health']}/100",
        "",
        "**趋势**",
    ]
    for m in ev["metrics"]:
        lines.append(
            f"- {m['month']}：消耗 {_fmt_money(float(m['spend']))} 元，转化 {m['conversions']} 条，"
            f"CVR {float(m['cvr']) * 100:.2f}%，ROAS {float(m['roas']):.1f}"
        )
    lines.append(f"- 近 30 天消耗环比{trend}（{delta:+.1f}%）")

    lines += ["", "**风险信号**"]
    if ev["signals"]:
        for s in ev["signals"]:
            lines.append(f"- {s['label']}：{s['detail']}")
    else:
        lines.append("- 未命中流失预警规则")

    lines += ["", "**近期跟进**"]
    if ev["interactions"]:
        for it in ev["interactions"][:3]:
            lines.append(f"- {it['date']} {it['channel']}（{it['sentiment']}）：{it['summary']}")
    else:
        lines.append("- 暂无跟进记录")

    lines += ["", "**建议动作**"]
    if top_opp:
        lines.append(f"- 优先：{top_opp['type']}——{top_opp['reason']}")
        for o in ev["opportunities"][1:3]:
            lines.append(f"- 其次：{o['type']}——{o['reason']}")
    lines.append(f"- 跟进节奏：按{layer}层标准动作，{_cadence(layer)}")
    return "\n".join(lines)


def _verdict(opp: dict, ev: dict) -> str:
    """把商机类型翻译成一句人话判断，避免"效果达标但最该做挽留"这种自相矛盾的表述。"""
    adv = ev["advertiser"]
    days = scoring.days_until(adv["plan_expire_date"])
    return {
        "流失挽留": "存在流失风险，优先稳住客户而不是加量",
        "续费": f"{days} 天后到期，已进入续费窗口，先谈续费",
        "扩量增投": "效果达标且预算跑满，可以谈增投",
        "升级套餐": "消耗已到标准版区间，建议升级套餐",
        "效果优化": "转化率在下滑，先修承接再谈量",
        "激活投放": f"预算只用了 {ev['utilization']}%，先解决投不出去的问题",
    }.get(opp["type"], "维持当前节奏，按分层标准动作跟进")


def _cadence(layer: str) -> str:
    return {
        "S": "每周复盘、季度提案",
        "A": "双周复盘、月度提案",
        "B": "月度复盘 + 素材支持",
        "C": "首月每周跟进，重点防流失",
    }.get(layer, "月度复盘")


def profile_node(state: AgentState, ctx: Context) -> dict:
    advertiser_id = state.get("advertiser_id")
    trace = list(state.get("trace", []))

    if not advertiser_id:
        top = ctx.store.list_advertisers(limit=5)
        trace.append({"step": "profile", "detail": "未指定客户，返回候选列表"})
        body = "\n".join(
            f"- {a['advertiser_id']} {a['name']}（{a['industry']}，消耗 {_fmt_money(float(a['spend_30d']))} 元）"
            for a in top
        )
        return {
            "answer": (
                "要出客户画像，得先确定是哪个客户。直接在问题里带上客户名或编号就行，比如"
                "「A003 的画像」。\n\n当前消耗最高的 5 个客户：\n" + body +
                "\n\n如果是要看今天该跟进谁，问「今天该优先跟进哪些客户」会给你一份按分数排序的名单。"
            ),
            "evidence": {"candidates": top},
            "citations": [],
            "trace": trace,
        }

    ev = build_evidence(ctx, advertiser_id)
    if not ev:
        return {"answer": f"没有找到客户 {advertiser_id} 的数据。", "evidence": {},
                "citations": [], "trace": trace}

    trace.append({"step": "profile", "detail": f"读取 {advertiser_id} 档案/计划/指标/跟进记录",
                  "advertiser_id": advertiser_id})

    answer = None
    if ctx.llm.available:
        answer = ctx.llm.chat([
            {"role": "system", "content":
             "你是广告代理商的中小客户运营专家。基于给定的结构化数据写一份客户画像，"
             "必须：1) 先给一句可直接复述的判断；2) 所有结论都要带具体数字；"
             "3) 风险与建议分开写；4) 不要编造数据里没有的信息；5) 用 Markdown。"},
            {"role": "user", "content": _evidence_to_prompt(ev)},
        ], temperature=0.3, max_tokens=900)

    if not answer:
        answer = render_template(ev)
        trace.append({"step": "profile", "detail": "模型不可用，使用规则模板生成画像"})
    else:
        trace.append({"step": "profile", "detail": "模型生成画像卡"})

    return {"answer": answer, "evidence": _slim(ev), "citations": [], "trace": trace}


def _evidence_to_prompt(ev: dict) -> str:
    adv = ev["advertiser"]
    campaigns = [f"{c['channel']}/{c['status']}/消耗{_fmt_money(float(c['spend_30d']))}" for c in ev["campaigns"]]
    metrics = [f"{m['month']}: 消耗{_fmt_money(float(m['spend']))}, 转化{m['conversions']}, CVR{float(m['cvr'])*100:.2f}%" for m in ev["metrics"]]
    return (
        f"客户：{adv['name']}（{adv['advertiser_id']}），{adv['industry']}，{adv['region']}，{adv['employee_scale']} 人\n"
        f"套餐：{adv['plan']}，{adv['plan_expire_date']} 到期\n"
        f"月预算：{adv['monthly_budget']}，近30天消耗：{adv['spend_30d']}，上期消耗：{adv['spend_prev_30d']}，利用率 {ev['utilization']}%\n"
        f"CVR：{adv['cvr']}，CPC：{adv['cpc']}，ROAS：{adv['roas']}，转化数：{adv['conversions_30d']}\n"
        f"分层：{ev['layer']}，健康度：{ev['health']}，风险等级：{ev['risk_level']}\n"
        f"计划：{'；'.join(campaigns)}\n"
        f"月度指标：{'；'.join(metrics)}\n"
        f"风险信号：{'；'.join(s['label'] + '-' + s['detail'] for s in ev['signals']) or '无'}\n"
        f"近期跟进：{'；'.join(i['date'] + i['sentiment'] + i['summary'] for i in ev['interactions'][:3]) or '无'}\n"
        f"规则给出的商机：{'；'.join(o['type'] + '(' + str(o['score']) + '分)' for o in ev['opportunities'])}\n"
        f"分层运营节奏：{_cadence(ev['layer'])}"
    )


def _slim(ev: dict) -> dict:
    """返回给前端的证据要瘦身，避免把整张表传过去。"""
    return {
        "advertiser_id": ev["advertiser"]["advertiser_id"],
        "name": ev["advertiser"]["name"],
        "layer": ev["layer"],
        "health": ev["health"],
        "risk_level": ev["risk_level"],
        "utilization": ev["utilization"],
        "signals": [s["label"] for s in ev["signals"]],
        "opportunities": [{"type": o["type"], "score": o["score"]} for o in ev["opportunities"]],
    }
