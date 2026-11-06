"""经营分析 Agent：Text-to-SQL + 归因提示。

分析类问题的坑在于"出数就完事"。销售真正要的是"为什么掉、下一步调什么"，
所以这里固定输出三段：结论 → 明细 → 归因提示（带可验证方向）。
"""

from __future__ import annotations

import re
from typing import Any

from ..context import Context
from ..state import AgentState
from ..tools import sql_agent
from .router import is_compare


def _attribution(ctx: Context, question: str, rows: list[dict]) -> list[str]:
    """不猜玄学，只给能被数据验证的方向。"""
    hints: list[str] = []
    q = question

    if any(k in q for k in ("下滑", "下降", "掉了", "掉量")):
        hints.append("先确认是量的问题还是价的问题：拆开看消耗、点击量、转化率三条线，只有 CVR 掉才是承接问题")
        hints.append("检查近 30 天是否有计划被暂停——停掉的主力计划会直接拉低整体消耗")

    if any(k in q for k in ("cvr", "转化率", "线索")):
        paused = ctx.store.query(
            "SELECT COUNT(*) AS n FROM campaigns WHERE status != '投放中'"
        )[0]["n"]
        hints.append(f"全库当前有 {paused} 条非投放中计划，检查是否包含这些客户的主力计划")
        hints.append("转化率类问题按「渠道 → 素材 → 落地页表单」三层排查，优先看表单字段数量")

    if any(k in q for k in ("续费", "到期")):
        hints.append("续费窗口内优先谈增值项而不是降价，降价会拉低后续所有客户的锚点")

    if any(k in q for k in ("预算", "利用率")):
        hints.append("利用率低先解决曝光不足（出价/定向/素材数量），不要直接谈增投")

    if is_compare(q):
        hints.append("比较类结论要拆开看：消耗看规模、CVR 看承接、CPA 看效率，三者方向不一致时不要急着下单一结论")
        hints.append("跨客户对比还要看体量：小客户 ROAS 高可能只是基数小，不能直接当增投依据")

    if not hints:
        hints.append("结论可直接用于早会同步；如需归因，请补充时间范围或对比维度")
    return hints


PREFERRED_METRICS = ("消耗合计", "消耗", "spend", "金额", "得分", "占比", "百分比")

DISPLAY = {
    "spend": ("消耗", "元"),
    "cvr": ("CVR百分比", "%"),
    "roas": ("ROAS", ""),
    "cpa": ("转化成本", "元"),
    "cpc": ("点击成本", "元"),
    "conversions": ("转化数", ""),
}
LOWER_IS_BETTER = {"cpa", "cpc"}


def _fmt_num(v: Any) -> str:
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return f"{v:,.0f}" if abs(v - round(v)) < 1e-9 else f"{v:,.2f}"
    return str(v)


def _pct(old: float, new: float) -> str:
    if not old:
        return "不可比"
    change = (new - old) / old
    sign = "+" if change >= 0 else ""
    return f"{sign}{change * 100:.1f}%"


def _compare_conclusion(rows: list[dict], question: str) -> str | None:
    """比较类问题的结论必须点名"谁"或"哪个月"，并给出两边的真实数字。"""
    if not rows:
        return None
    cols = rows[0].keys()

    # 情形一：单客户月度环比（模板输出 month 列）
    if "月份" in cols and len(rows) >= 2:
        prev, cur = rows[-2], rows[-1]
        parts, good, bad = [], 0, 0
        for key, label, _unit in (
            ("消耗", "消耗", ""), ("CVR百分比", "CVR", "%"),
            ("ROAS", "ROAS", ""), ("转化成本", "转化成本", "元"),
        ):
            if key not in cur or cur.get(key) is None or prev.get(key) is None:
                continue
            p, c = float(prev[key] or 0), float(cur[key] or 0)
            parts.append(f"{label} {_fmt_num(p)}→{_fmt_num(c)}（{_pct(p, c)}）")
            better_is_up = key != "转化成本"
            improved = (c > p) if better_is_up else (c < p)
            if p and abs((c - p) / p) > 0.03:
                good += 1 if improved else 0
                bad += 0 if improved else 1
        verdict = "整体变好" if good > bad else ("整体变差" if bad > good else "有好有差")
        main = DISPLAY.get(sql_agent.pick_metric(question), ("消耗", ""))[0]
        who = f"{cur.get('客户')} " if cur.get("客户") else ""
        return (f"{who}{cur.get('月份')} vs {prev.get('月份')}：{'；'.join(parts)}。"
                f"{verdict}，主要矛盾看「{main}」。")

    # 情形二：跨客户对比（模板输出多个 advertiser_id）
    if len(rows) >= 2 and "advertiser_id" in cols:
        metric = sql_agent.pick_metric(question)
        col = "对比指标" if "对比指标" in cols else DISPLAY.get(metric, ("消耗", ""))[0]
        values = [(float(r.get(col) or 0), r) for r in rows]
        want_min = any(w in question for w in ("更低", "最低", "更差", "最差", "更少", "谁低", "谁差"))
        win = min(values, key=lambda x: x[0]) if want_min else max(values, key=lambda x: x[0])
        others = [f"{r.get('name') or r.get('advertiser_id')} {_fmt_num(v)}"
                  for v, r in values if r is not win[1]]
        direction = "更低" if want_min else "更高"
        label = DISPLAY.get(metric, (col, ""))[0]
        return (f"{win[1].get('name') or win[1].get('advertiser_id')} 的 {label} {direction}"
                f"（{_fmt_num(win[0])}）；对比：{'、'.join(others)}。")
    return None


def _conclusion(rows: list[dict], question: str) -> str:
    """挑一个最能说明问题的指标做结论，而不是无脑读第一列。"""
    if not rows:
        return "查询没有返回数据，可能是筛选条件过严或数据口径不一致。"

    compared = _compare_conclusion(rows, question) if is_compare(question) else None
    if compared:
        return compared

    numeric_keys = [k for k, v in rows[0].items() if isinstance(v, (int, float))]
    if not numeric_keys:
        return f"共返回 {len(rows)} 条记录。"

    key = next((k for k in numeric_keys if any(p in str(k) for p in PREFERRED_METRICS)), numeric_keys[0])
    label_keys = [k for k in rows[0] if k not in numeric_keys]

    # 问"最低/最差/下滑"时结论要说最小值，否则会自相矛盾
    want_min = any(w in question for w in ("最低", "最差", "最少", "下滑", "下降"))
    picker = min if want_min else max
    best = picker(rows, key=lambda r: float(r.get(key) or 0))
    label = str(best.get(label_keys[0], "")) if label_keys else ""
    extreme = "最低" if want_min else "最高"

    if label_keys:
        return f"共 {len(rows)} 条记录；按「{key}」看，{label} {extreme}（{_fmt_num(best[key])}）。"
    return f"共 {len(rows)} 条记录；「{key}」合计 {_fmt_num(sum(float(r[key] or 0) for r in rows))}。"


def analysis_node(state: AgentState, ctx: Context) -> dict:
    question = state.get("question", "")
    trace = list(state.get("trace", []))

    sql, source = sql_agent.nl_to_sql(question, ctx.llm, state.get("advertiser_id"))
    trace.append({"step": "analysis", "detail": f"生成 SQL（来源：{source}）", "sql": sql})

    if not sql:
        return {
            "answer": "这个问题我暂时没法转成查询。可以换个说法，比如「各行业消耗汇总」「转化率最低的 10 个客户」"
                      "「哪些客户计划快到期」。",
            "evidence": {},
            "citations": [],
            "trace": trace,
        }

    ok, reason = sql_agent.guard(sql)
    if not ok:
        trace.append({"step": "analysis", "detail": f"SQL 被安全校验拦截：{reason}"})
        return {"answer": f"这条查询没通过安全校验（{reason}），需要人工确认后再执行。",
                "evidence": {"sql": sql}, "citations": [], "trace": trace}

    try:
        rows = sql_agent.run_readonly(ctx.settings.db_path, sql)
    except Exception as exc:  # noqa: BLE001 - 查询失败要给出可解释的提示，而不是抛栈
        trace.append({"step": "analysis", "detail": f"执行失败：{exc}"})
        return {"answer": f"查询执行失败：{exc}", "evidence": {"sql": sql}, "citations": [], "trace": trace}

    table = sql_agent.format_rows(rows)
    hints = _attribution(ctx, question, rows)
    conclusion = _conclusion(rows, question)

    answer = None
    if ctx.llm.available:
        answer = ctx.llm.chat([
            {"role": "system", "content":
             "你是广告代理商的数据分析师。基于查询结果写一段经营分析，要求：先给结论、再解释关键数字、"
             "最后给 2 条可执行的归因方向；不要编造结果里没有的数字；用 Markdown。"},
            {"role": "user", "content": f"问题：{question}\nSQL：{sql}\n结果：\n{table}"},
        ], temperature=0.3, max_tokens=700)

    if not answer:
        lines = [
            "### 经营分析",
            "",
            f"**结论**：{conclusion}",
            "",
            "**查询**",
            f"```sql\n{sql}\n```",
            "",
            "**明细**",
            f"```\n{table}\n```",
            "",
            "**归因提示**",
        ]
        lines += [f"- {h}" for h in hints]
        answer = "\n".join(lines)
        trace.append({"step": "analysis", "detail": "模型不可用，使用模板汇总查询结果"})
    else:
        trace.append({"step": "analysis", "detail": f"模型完成分析（{len(rows)} 行结果）"})

    return {
        "answer": answer,
        "evidence": {"sql": sql, "row_count": len(rows), "rows": rows[:8], "source": source},
        "citations": [],
        "trace": trace,
    }


def looks_like_analysis(text: str) -> bool:
    return bool(re.search(r"(多少|统计|排名|汇总|环比|名单|top|最高|最低)", text, re.I))
