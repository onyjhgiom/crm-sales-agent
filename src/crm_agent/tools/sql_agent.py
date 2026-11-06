"""分析类问题的 Text-to-SQL：模型生成 + 模板兜底 + 安全校验。

安全设计（这是整个项目里最容易被问到的点）：
- 生成出来的 SQL 必须过 `guard()`：只允许单条 SELECT、禁止 DDL/DML、
  禁止 PRAGMA/ATTACH、只允许访问白名单表；
- 用只读连接执行（SQLite URI mode=ro），即使 guard 被判错也无法写库；
- 模板兜底保证模型不可用或生成失败时，常见统计问题仍能出数。

为什么要两层：大模型写 SQL 准确率不稳定，面试官一定会问"写错了怎么办"，
答案就是"白名单 + 只读连接 + 模板兜底 + 结果行数上限"。
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

ALLOWED_TABLES = {"advertisers", "campaigns", "metrics", "interactions"}
FORBIDDEN = re.compile(
    r"(insert|update|delete|drop|alter|create|attach|detach|pragma|vacuum|replace|trigger|;.*;)",
    re.I,
)

SCHEMA_DESC = """表结构（SQLite）：
advertisers(advertiser_id, name, industry, region, employee_scale, plan, onboard_date,
  plan_expire_date, monthly_budget, spend_30d, spend_prev_30d, active_campaigns,
  impressions_30d, clicks_30d, conversions_30d, cvr, cpc, roas, main_objective,
  last_contact_date, notes)
campaigns(campaign_id, advertiser_id, name, channel, objective, status, spend_30d,
  budget_cap, impressions, clicks, conversions)
metrics(month, advertiser_id, spend, impressions, clicks, conversions, cvr, cpa, roas)
interactions(interaction_id, advertiser_id, date, channel, summary, sentiment, next_action)
口径：spend_30d 为近 30 天消耗（元）；cvr 为转化率小数；roas 为投入产出比。
"""


def guard(sql: str) -> tuple[bool, str]:
    """返回 (是否放行, 原因)。任何一条不满足就拒绝执行。"""
    sql = sql.strip().rstrip(";")
    if not sql:
        return False, "空 SQL"
    if FORBIDDEN.search(sql):
        return False, "包含非只读关键字"
    if not re.match(r"select\b", sql, re.I):
        return False, "只允许 SELECT"
    tables = set(re.findall(r"(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, re.I))
    tables = {t.lower() for t in tables}
    illegal = tables - ALLOWED_TABLES
    if illegal:
        return False, f"访问了非白名单表：{sorted(illegal)}"
    if ";" in sql:
        return False, "疑似多条语句"
    return True, "ok"


def run_readonly(db_path: str, sql: str, limit: int = 50) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql)
        rows = [dict(r) for r in cur.fetchmany(limit)]
        return rows
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 模板兜底：覆盖作品集演示/评估里出现的高频问题
# --------------------------------------------------------------------------
# 比较类问题要先判断"比什么指标"，否则回答会跑偏到消耗
METRIC_ALIASES = (
    ("roas", ("roas", "投产比", "投产")),
    ("cvr", ("cvr", "转化率")),
    ("cpc", ("cpc", "点击成本")),
    ("cpa", ("cpa", "转化成本", "获客成本")),
    ("conversions", ("转化数", "转化量", "线索量")),
    ("spend", ("消耗", "花费", "花销", "金额")),
)


def pick_metric(question: str) -> str:
    low = question.lower()
    for key, words in METRIC_ALIASES:
        if any(w in low for w in words):
            return key
    return "spend"


METRIC_EXPR = {
    "roas": "ROUND(roas,2)",
    "cvr": "ROUND(cvr*100,2)",
    "cpc": "ROUND(cpc,2)",
    # advertisers 表没有现成 cpa，用消耗/转化数现算，NULLIF 防止除零
    "cpa": "ROUND(spend_30d / NULLIF(conversions_30d,0), 2)",
    "conversions": "conversions_30d",
    "spend": "ROUND(spend_30d)",
}


def template_sql(question: str, advertiser_id: str | None = None) -> str | None:
    q = question

    # ---- 比较类：跨客户对比（"A003 和 A011 谁的 ROAS 更高"）----
    ids = re.findall(r"[Aa]\d{3}", q)
    ids = list(dict.fromkeys(x.upper() for x in ids))
    if len(ids) >= 2:
        metric = pick_metric(q)
        return (
            "SELECT advertiser_id, name, industry, ROUND(spend_30d) AS 近30天消耗, "
            "roas AS ROAS, ROUND(cvr*100,2) AS CVR百分比, ROUND(cpc,2) AS 点击成本, "
            f"{METRIC_EXPR[metric]} AS 对比指标 "
            "FROM advertisers WHERE advertiser_id IN ("
            + ", ".join(f"'{i}'" for i in ids)
            + ") ORDER BY 对比指标 DESC"
        )

    # ---- 比较类：单客户环比（"这个月比上个月好还是差"）----
    if any(k in q for k in ("比上个月", "比上月", "环比", "同比", "好还是差", "走势", "趋势")) and advertiser_id:
        return (
            "SELECT m.month AS 月份, a.name AS 客户, ROUND(m.spend) AS 消耗, "
            "ROUND(m.cvr*100,2) AS CVR百分比, ROUND(m.cpa) AS 转化成本, "
            "ROUND(m.roas,2) AS ROAS FROM metrics m "
            "JOIN advertisers a ON a.advertiser_id = m.advertiser_id "
            f"WHERE m.advertiser_id = '{advertiser_id}' ORDER BY m.month"
        )

    if "行业" in q and ("汇总" in q or "对比" in q or "分布" in q or "各" in q):
        return (
            "SELECT industry, COUNT(*) AS 客户数, ROUND(SUM(spend_30d)) AS 消耗合计, "
            "ROUND(AVG(roas),2) AS 平均ROAS, ROUND(AVG(cvr*100),2) AS 平均CVR百分比 "
            "FROM advertisers GROUP BY industry ORDER BY 消耗合计 DESC"
        )
    if ("下滑" in q or "下降" in q or "掉了" in q) and "消耗" in q:
        return (
            "SELECT advertiser_id, name, industry, ROUND(spend_prev_30d) AS 上期消耗, "
            "ROUND(spend_30d) AS 本期消耗, "
            "ROUND((spend_prev_30d - spend_30d) / spend_prev_30d * 100, 1) AS 下滑百分比 "
            "FROM advertisers WHERE spend_prev_30d > 0 "
            "AND (spend_prev_30d - spend_30d) / spend_prev_30d >= 0.2 "
            "ORDER BY 下滑百分比 DESC"
        )
    if "到期" in q or "续费" in q:
        return (
            "SELECT advertiser_id, name, plan, plan_expire_date, "
            "CAST(julianday(plan_expire_date) - julianday('now') AS INT) AS 剩余天数, "
            "ROUND(spend_30d) AS 近30天消耗 FROM advertisers "
            "WHERE julianday(plan_expire_date) - julianday('now') BETWEEN 0 AND 30 "
            "ORDER BY 剩余天数"
        )
    if "roas" in q.lower() and ("最高" in q or "top" in q.lower() or "前" in q):
        return (
            "SELECT advertiser_id, name, industry, ROUND(spend_30d) AS 消耗, roas, "
            "ROUND(cvr*100,2) AS CVR百分比 FROM advertisers "
            "ORDER BY roas DESC LIMIT 10"
        )
    if "cvr" in q.lower() or "转化率" in q:
        if "低" in q or "差" in q:
            order = "ASC"
        else:
            order = "DESC"
        return (
            "SELECT advertiser_id, name, industry, ROUND(cvr*100,2) AS CVR百分比, "
            "conversions_30d AS 转化数, ROUND(spend_30d) AS 消耗 FROM advertisers "
            f"ORDER BY cvr {order} LIMIT 10"
        )
    if "预算" in q and ("利用率" in q or "没花完" in q or "跑满" in q):
        return (
            "SELECT advertiser_id, name, ROUND(monthly_budget) AS 月预算, "
            "ROUND(spend_30d) AS 近30天消耗, "
            "ROUND(spend_30d / monthly_budget * 100, 1) AS 利用率百分比 "
            "FROM advertisers WHERE monthly_budget > 0 "
            "ORDER BY 利用率百分比 ASC LIMIT 15"
        )
    if "计划" in q and ("暂停" in q or "停投" in q):
        return (
            "SELECT advertiser_id, campaign_id, channel, status, ROUND(spend_30d) AS 消耗 "
            "FROM campaigns WHERE status != '投放中' ORDER BY spend_30d DESC LIMIT 20"
        )
    if "消耗" in q and ("总" in q or "合计" in q or "多少" in q):
        return (
            "SELECT ROUND(SUM(spend_30d)) AS 消耗合计, COUNT(*) AS 客户数, "
            "ROUND(AVG(spend_30d)) AS 户均消耗 FROM advertisers"
        )
    if "负面" in q or "投诉" in q or "不满" in q:
        return (
            "SELECT i.advertiser_id, a.name, i.date, i.summary FROM interactions i "
            "JOIN advertisers a ON a.advertiser_id = i.advertiser_id "
            "WHERE i.sentiment = '负面' ORDER BY i.date DESC LIMIT 20"
        )
    return None


def nl_to_sql(question: str, llm, advertiser_id: str | None = None) -> tuple[str | None, str]:
    """返回 (sql, 来源)。来源用于评估时区分模型能力与兜底能力。"""
    if llm is not None and llm.available:
        out = llm.chat(
            [
                {
                    "role": "system",
                    "content": "你是数据分析助手，只输出一条 SQLite SELECT 语句，"
                               "不要解释、不要代码块标记、不要分号结尾。",
                },
                {"role": "user", "content": f"{SCHEMA_DESC}\n问题：{question}\nSQL："},
            ],
            temperature=0.0,
            max_tokens=300,
        )
        if out:
            sql = out.strip().strip("`").strip()
            if sql.lower().startswith("sql"):
                sql = sql[3:].strip()
            ok, _reason = guard(sql)
            if ok:
                return sql, "llm"
    sql = template_sql(question, advertiser_id)
    return (sql, "template") if sql else (None, "none")


def format_rows(rows: list[dict[str, Any]], max_rows: int = 8) -> str:
    if not rows:
        return "（查询结果为空）"
    cols = list(rows[0].keys())
    lines = [" | ".join(cols)]
    lines.append("-" * len(lines[0]))
    for row in rows[:max_rows]:
        lines.append(" | ".join(str(row.get(c, "")) for c in cols))
    if len(rows) > max_rows:
        lines.append(f"... 共 {len(rows)} 行，仅展示前 {max_rows} 行")
    return "\n".join(lines)
