"""SQLite 数据访问层。

只做两件事：把行转成 dict、把常用查询收敛成方法。
所有上层节点都不直接写 SQL（除了分析 Agent 里经过白名单校验的 Text-to-SQL），
避免 SQL 散落在提示词和业务逻辑之间。

并发：每个工作线程持有自己的只读连接（threading.local + mode=ro）。
原先全局共用一个可写连接，在 FastAPI 线程池下并发会抛 InterfaceError；
简单加一把全局锁虽然能消错，却把所有查询串行化，并发延迟反而变差。
只读连接既能并行读、天然线程安全，也顺手堵死了"业务查询误写库"这条路。
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any, Iterable


class CRMStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._all_lock = threading.Lock()

    def _connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
            with self._all_lock:
                self._all.append(conn)
        return conn

    def close(self) -> None:
        with self._all_lock:
            conns, self._all = self._all, []
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        cur = self._connection().execute(sql, tuple(params))
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
