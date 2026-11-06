"""生成一套模拟的中小广告主 CRM 数据，用于演示与评估。

为什么要自己造数据：
1. 真实广告主数据涉及客户隐私，不能进公开作品集；
2. 演示需要"能被追问"的细节——消耗、转化、跟进记录必须彼此自洽，
   否则面试官一眼看出是假数据。

设计原则：
- 固定随机种子，任何人 clone 下来执行 `python data/generate_data.py` 得到完全相同的数据；
- 表之间强一致：计划消耗之和 = 广告主 30 天消耗，月度指标最后一个月与 30 天消耗对齐；
- 刻意埋入可解释的业务信号：消耗下滑、转化率走低、计划快到期、长时间未跟进。

运行：python data/generate_data.py
产出：data/crm.db
"""

from __future__ import annotations

import datetime as dt
import os
import random
import sqlite3

SEED = 20261106
TODAY = dt.date(2026, 11, 6)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crm.db")

# 行业 -> (常见 CPC 区间, 常见 CVR 区间, 客单价量级说明)
INDUSTRIES = {
    "本地生活": ((1.2, 2.4), (0.03, 0.07)),
    "教育培训": ((2.0, 3.6), (0.05, 0.09)),
    "家装建材": ((2.4, 4.2), (0.02, 0.05)),
    "医疗美容": ((3.0, 5.0), (0.03, 0.06)),
    "汽车服务": ((1.8, 3.2), (0.02, 0.04)),
    "B2B 软件": ((3.2, 6.0), (0.01, 0.03)),
    "电商零售": ((0.9, 2.0), (0.04, 0.10)),
    "招商加盟": ((2.6, 5.2), (0.01, 0.03)),
}

REGIONS = ["上海", "杭州", "南京", "苏州", "合肥", "宁波", "无锡", "常州"]

PLANS = ["基础版", "标准版", "旗舰版"]

NAME_POOL = [
    "青禾", "云栖", "拾光", "木白", "沿海", "知野", "山屿", "合川", "星野", "北望",
    "长亭", "鹿鸣", "南枝", "井巷", "春野", "白鹿", "禾下", "橙里", "麦田", "如初",
    "临溪", "见山", "晓雾", "青川", "初见", "半夏", "满城", "一苇", "浮光", "溪桥",
    "望舒", "海盐", "木犀", "青棠", "砚台", "沧浪", "鹿野", "白露", "朔方", "葭月",
    "长风", "疏影",
]

SUFFIX = {
    "本地生活": ["生活服务", "到店餐饮", "连锁洗衣", "宠物服务"],
    "教育培训": ["教育科技", "职业教育", "少儿编程", "考研培训"],
    "家装建材": ["家居", "全屋定制", "建材", "装修设计"],
    "医疗美容": ["医美", "口腔门诊", "皮肤管理", "眼科门诊"],
    "汽车服务": ["汽车服务", "汽修连锁", "汽车美容", "二手车"],
    "B2B 软件": ["科技", "数据", "云服务", "软件"],
    "电商零售": ["电商", "优选", "百货", "生鲜"],
    "招商加盟": ["餐饮管理", "连锁品牌", "创业孵化", "加盟服务"],
}

OBJECTIVES = ["线索收集", "到店转化", "App 下载", "商品成交"]
CHANNELS = ["搜索推广", "信息流", "开屏", "品牌专区"]

CALL_SUMMARIES_POS = [
    "客户认可当前投放带来的线索量，询问能否再扩两个城市",
    "客户反馈本季度新店开业，希望加大到店类投放",
    "客户主动咨询旗舰版权益，关注素材制作能力",
    "客户对新上的创意素材点击率满意，同意下周复盘",
]

CALL_SUMMARIES_NEU = [
    "客户觉得线索质量一般，要求核对表单有效性",
    "客户提出后台数据与自家 CRM 对不上，需要排查",
    "客户预算被总部压缩，本季度暂缓追加",
    "客户希望下调 CPC，但对曝光量有要求，需要平衡",
]

CALL_SUMMARIES_NEG = [
    "客户反馈近两周消耗掉得很快但转化少，情绪不满",
    "客户提到同行换了别的渠道，正在比价",
    "客户表示负责人变动，暂时无法推进续费",
    "客户抱怨开户流程太长，影响投放节奏",
]


def _d(days_ago: int) -> str:
    return (TODAY - dt.timedelta(days=days_ago)).isoformat()


def _months(n: int = 3) -> list[str]:
    out = []
    y, m = TODAY.year, TODAY.month
    for _ in range(n):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        out.append(f"{y}-{m:02d}")
    return list(reversed(out))


def build_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS advertisers;
        DROP TABLE IF EXISTS campaigns;
        DROP TABLE IF EXISTS metrics;
        DROP TABLE IF EXISTS interactions;

        CREATE TABLE advertisers (
            advertiser_id      TEXT PRIMARY KEY,
            name               TEXT NOT NULL,
            industry           TEXT NOT NULL,
            region             TEXT NOT NULL,
            employee_scale     INTEGER NOT NULL,   -- 员工人数，用来粗判客户体量
            plan               TEXT NOT NULL,
            onboard_date       TEXT NOT NULL,
            plan_expire_date   TEXT NOT NULL,
            monthly_budget     REAL NOT NULL,      -- 客户自报月预算
            spend_30d          REAL NOT NULL,      -- 近 30 天实际消耗
            spend_prev_30d     REAL NOT NULL,      -- 上一个 30 天消耗
            active_campaigns   INTEGER NOT NULL,
            impressions_30d    INTEGER NOT NULL,
            clicks_30d         INTEGER NOT NULL,
            conversions_30d    INTEGER NOT NULL,
            cvr                REAL NOT NULL,
            cpc                REAL NOT NULL,
            roas               REAL NOT NULL,
            main_objective     TEXT NOT NULL,
            last_contact_date  TEXT NOT NULL,
            notes              TEXT
        );

        CREATE TABLE campaigns (
            campaign_id    TEXT PRIMARY KEY,
            advertiser_id  TEXT NOT NULL,
            name           TEXT NOT NULL,
            channel        TEXT NOT NULL,
            objective      TEXT NOT NULL,
            status         TEXT NOT NULL,   -- 投放中 / 已暂停 / 已结束
            spend_30d      REAL NOT NULL,
            budget_cap     REAL NOT NULL,   -- 计划日预算上限 * 30
            impressions    INTEGER NOT NULL,
            clicks         INTEGER NOT NULL,
            conversions    INTEGER NOT NULL
        );

        CREATE TABLE metrics (
            month          TEXT NOT NULL,
            advertiser_id  TEXT NOT NULL,
            spend          REAL NOT NULL,
            impressions    INTEGER NOT NULL,
            clicks         INTEGER NOT NULL,
            conversions    INTEGER NOT NULL,
            cvr            REAL NOT NULL,
            cpa            REAL NOT NULL,
            roas           REAL NOT NULL,
            PRIMARY KEY (month, advertiser_id)
        );

        CREATE TABLE interactions (
            interaction_id TEXT PRIMARY KEY,
            advertiser_id  TEXT NOT NULL,
            date           TEXT NOT NULL,
            channel        TEXT NOT NULL,   -- 电话 / 微信 / 拜访 / 线上会议
            summary        TEXT NOT NULL,
            sentiment      TEXT NOT NULL,   -- 正面 / 中性 / 负面
            next_action    TEXT
        );

        CREATE INDEX idx_campaigns_adv ON campaigns(advertiser_id);
        CREATE INDEX idx_metrics_adv ON metrics(advertiser_id);
        CREATE INDEX idx_inter_adv ON interactions(advertiser_id);
        """
    )


def build_data(conn: sqlite3.Connection) -> None:
    rng = random.Random(SEED)
    months = _months(3)

    for i in range(42):
        industry = list(INDUSTRIES)[i % len(INDUSTRIES)]
        cpc_lo, cpc_hi = INDUSTRIES[industry][0]
        cvr_lo, cvr_hi = INDUSTRIES[industry][1]

        adv_id = f"A{i + 1:03d}"
        name = f"{NAME_POOL[i]}{rng.choice(SUFFIX[industry])}"
        region = rng.choice(REGIONS)
        employees = rng.choice([12, 23, 35, 48, 60, 85, 120, 180, 260, 400])
        plan = rng.choices(PLANS, weights=[3, 3, 2])[0]
        onboard_days = rng.choice([40, 65, 120, 180, 300, 420, 600, 800])
        onboard_date = _d(onboard_days)

        monthly_budget = rng.choice([15000, 22000, 30000, 45000, 60000, 80000, 120000])
        # 消耗相对预算的利用率，埋在 0.35~1.0 之间，低利用率本身就是"可挖掘空间"信号
        util = rng.choice([0.35, 0.55, 0.72, 0.85, 0.95, 1.0])
        spend_30d = round(monthly_budget * util, 2)
        growth = rng.choice([-0.32, -0.18, -0.05, 0.0, 0.06, 0.15, 0.28, 0.42])
        spend_prev = round(spend_30d / (1 + growth), 2) if growth != 0 else spend_30d

        cpc = round(rng.uniform(cpc_lo, cpc_hi), 2)
        cvr = round(rng.uniform(cvr_lo, cvr_hi), 4)
        clicks = int(spend_30d / cpc)
        impressions = int(clicks / rng.uniform(0.012, 0.028))
        conversions = int(clicks * cvr)
        roas = round(rng.uniform(0.9, 3.6), 2)
        objective = rng.choice(OBJECTIVES)
        last_contact_days = rng.choice([2, 4, 7, 11, 16, 23, 31, 46, 62])

        plan_expire_days = rng.choice([8, 18, 27, 45, 70, 110, 160, 210])

        conn.execute(
            "INSERT INTO advertisers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                adv_id, name, industry, region, employees, plan, onboard_date,
                (TODAY + dt.timedelta(days=plan_expire_days)).isoformat(),
                monthly_budget, spend_30d, spend_prev,
                0,  # active_campaigns 稍后回填
                impressions, clicks, conversions, cvr, cpc, roas,
                objective, _d(last_contact_days),
                None if rng.random() > 0.35 else rng.choice([
                    "门店新开业，重点吃到店线索",
                    "总部要求本季度 ROI 提升 15%",
                    "自有 CRM 与投放后台数据口径不一致",
                    "同行比价中，价格敏感",
                    "重点推新品，需要素材支持",
                ]),
            ),
        )

        # ---- 计划：拆成 1~3 条，消耗之和等于广告主 30 天消耗 ----
        n_camp = rng.choice([1, 2, 2, 3])
        weights = [rng.uniform(0.3, 0.8) for _ in range(n_camp)]
        norm = [w / sum(weights) for w in weights]
        active = 0
        for j in range(n_camp):
            camp_spend = round(spend_30d * norm[j], 2)
            camp_clicks = int(camp_spend / cpc)
            camp_impr = int(camp_clicks / rng.uniform(0.012, 0.028))
            camp_conv = int(camp_clicks * cvr)
            status = rng.choices(["投放中", "已暂停", "已结束"], weights=[7, 2, 1])[0]
            if status == "投放中":
                active += 1
            conn.execute(
                "INSERT INTO campaigns VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"{adv_id}-C{j + 1}", adv_id,
                    f"{name}-{rng.choice(CHANNELS)}-{j + 1}",
                    rng.choice(CHANNELS), objective, status,
                    camp_spend, round(monthly_budget * rng.uniform(0.3, 0.6), 2),
                    camp_impr, camp_clicks, camp_conv,
                ),
            )
        conn.execute(
            "UPDATE advertisers SET active_campaigns=? WHERE advertiser_id=?", (active, adv_id)
        )

        # ---- 月度指标：最后一个月与 30 天消耗对齐 ----
        for k, month in enumerate(months):
            if k == len(months) - 1:
                spend = spend_30d
                conv = conversions
            else:
                ratio = rng.uniform(0.82, 1.15)
                spend = round(spend_prev * ratio, 2)
                conv = int(spend / cpc * cvr * rng.uniform(0.85, 1.1))
            m_clicks = int(spend / cpc)
            m_impr = int(m_clicks / rng.uniform(0.012, 0.028))
            m_cvr = round(conv / m_clicks, 4) if m_clicks else 0.0
            m_cpa = round(spend / conv, 2) if conv else 0.0
            m_roas = round(rng.uniform(0.9, 3.6), 2)
            conn.execute(
                "INSERT INTO metrics VALUES (?,?,?,?,?,?,?,?,?)",
                (month, adv_id, spend, m_impr, m_clicks, conv, m_cvr, m_cpa, m_roas),
            )

        # ---- 跟进记录：1~5 条 ----
        n_inter = rng.choice([1, 2, 2, 3, 4, 5])
        for t in range(n_inter):
            sentiment = rng.choices(["正面", "中性", "负面"], weights=[4, 4, 3])[0]
            pool = {"正面": CALL_SUMMARIES_POS, "中性": CALL_SUMMARIES_NEU, "负面": CALL_SUMMARIES_NEG}[sentiment]
            conn.execute(
                "INSERT INTO interactions VALUES (?,?,?,?,?,?,?)",
                (
                    f"{adv_id}-I{t + 1}", adv_id,
                    _d(last_contact_days + t * rng.choice([3, 5, 7, 9])),
                    rng.choice(["电话", "微信", "拜访", "线上会议"]),
                    rng.choice(pool), sentiment,
                    rng.choice([
                        "下周发一版新素材",
                        "约运营一起做数据复盘",
                        "整理增投测算表给客户",
                        "核对表单有效性",
                        "暂无明确动作",
                    ]),
                ),
            )


def main() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    try:
        build_schema(conn)
        build_data(conn)
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM advertisers").fetchone()[0]
        print(f"已生成 {n} 个广告主 -> {DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
