"""SQLite 数据访问层。

只做两件事：把行转成 dict、把常用查询收敛成方法。
所有上层节点都不直接写 SQL（除了分析 Agent 里经过白名单校验的 Text-to-SQL），
避免 SQL 散落在提示词和业务逻辑之间。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable


class CRMStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self._conn.close()

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        cur = self._conn.execute(sql, tuple(params))
        return [dict(row) for row in cur.fetchall()]

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ---------- 广告主页 ----------
    def list_advertisers(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.query(
            "SELECT advertiser_id, name, industry, region, plan, monthly_budget, "
            "spend_30d, roas, last_contact_date FROM advertisers "
            "ORDER BY spend_30d DESC LIMIT ?",
            (limit,),
        )

    def get_advertiser(self, advertiser_id: str) -> dict[str, Any] | None:
        return self.query_one(
            "SELECT * FROM advertisers WHERE advertiser_id = ?", (advertiser_id,)
        )

    def get_campaigns(self, advertiser_id: str) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM campaigns WHERE advertiser_id = ? ORDER BY spend_30d DESC",
            (advertiser_id,),
        )

    def get_metrics(self, advertiser_id: str, limit: int = 3) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM metrics WHERE advertiser_id = ? ORDER BY month DESC LIMIT ?",
            (advertiser_id, limit),
        )
        return list(reversed(rows))

    def get_interactions(self, advertiser_id: str, limit: int = 5) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM interactions WHERE advertiser_id = ? "
            "ORDER BY date DESC LIMIT ?",
            (advertiser_id, limit),
        )

    def find_by_industry(self, industry: str, limit: int = 5) -> list[dict[str, Any]]:
        return self.query(
            "SELECT advertiser_id, name, industry, plan, spend_30d, roas, cvr "
            "FROM advertisers WHERE industry = ? ORDER BY roas DESC LIMIT ?",
            (industry, limit),
        )

    def search_advertisers(self, keyword: str, limit: int = 5) -> list[dict[str, Any]]:
        like = f"%{keyword}%"
        return self.query(
            "SELECT advertiser_id, name, industry, region, spend_30d FROM advertisers "
            "WHERE name LIKE ? OR industry LIKE ? OR region LIKE ? "
            "ORDER BY spend_30d DESC LIMIT ?",
            (like, like, like, limit),
        )


def open_store(db_path: str) -> CRMStore:
    return CRMStore(db_path)
