"""会话级上下文记忆。

为什么单独做一层，而不是只依赖 LangGraph 的 MemorySaver：
- checkpointer 保存的是「图状态快照」，生产模式为了性能会关掉它（USE_CHECKPOINTER=false），
  那时多轮上下文就没了；记忆不该和图的执行细节绑在一起。
- 这里保存的是「用户可见的一轮问答」：问题、回答摘要、意图、客户、时间。
  它既能喂给路由做指代消解，也能喂给模型当对话背景，还能通过 API 查看/清空。

边界：
- 每个 session 有轮数上限，每个进程有会话数上限，避免长连接把内存吃光；
- 回答只存摘要（前 N 字），不把整篇画像塞进上下文；
- 可选持久化到 SQLite（独立文件，不碰模拟数据 crm.db），重启后记忆仍在；
- 所有操作加锁；持久化失败只记日志，不影响主链路。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any

log = logging.getLogger("crm_agent.memory")


@dataclass
class Turn:
    role: str                       # user / assistant
    content: str                    # 用户问题 或 回答摘要
    intent: str | None = None       # assistant 轮的意图
    advertiser_id: str | None = None
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ts"] = round(self.ts, 3)
        return d


class ConversationMemory:
    """按 session_id 存放有界对话轮次，可选 SQLite 持久化。"""

    TABLE = "conversation_turns"

    def __init__(self, max_turns: int = 12, max_sessions: int = 128,
                 answer_chars: int = 240, db_path: str | None = None):
        self.max_turns = max(1, max_turns)
        self.max_sessions = max(1, max_sessions)
        self.answer_chars = max(40, answer_chars)
        self.db_path = db_path or ""
        self._lock = threading.RLock()
        self._sessions: OrderedDict[str, list[Turn]] = OrderedDict()
        self._conn: sqlite3.Connection | None = None
        if self.db_path:
            self._init_db()

    # ------------------------------------------------------------------
    def _init_db(self) -> None:
        try:
            parent = os.path.dirname(os.path.abspath(self.db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
            self._conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self.TABLE} ("
                "session_id TEXT NOT NULL, seq INTEGER NOT NULL, role TEXT NOT NULL, "
                "content TEXT NOT NULL, intent TEXT, advertiser_id TEXT, ts REAL NOT NULL, "
                "PRIMARY KEY (session_id, seq))"
            )
            self._conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self.TABLE}_sid "
                f"ON {self.TABLE}(session_id, seq)"
            )
            self._conn.commit()
            self._load()
        except Exception as exc:  # noqa: BLE001 - 持久化是增强，失败就退化为纯内存
            log.warning("对话记忆持久化不可用，已退化为纯内存：%s", type(exc).__name__)
            self._conn = None

    def _load(self) -> None:
        """启动时把库里的轮次读回内存，保持和运行时同一套上限。"""
        assert self._conn is not None
        rows = self._conn.execute(
            f"SELECT session_id, role, content, intent, advertiser_id, ts "
            f"FROM {self.TABLE} ORDER BY session_id, seq"
        ).fetchall()
        grouped: OrderedDict[str, list[Turn]] = OrderedDict()
        for sid, role, content, intent, adv, ts in rows:
            grouped.setdefault(sid, []).append(
                Turn(role=role, content=content, intent=intent, advertiser_id=adv, ts=ts)
            )
        for sid, turns in grouped.items():
            self._sessions[sid] = turns[-self.max_turns:]
        self._prune_sessions_locked()
        log.info("对话记忆已加载：%d 个会话，%d 轮", self.stats()["sessions"], self.stats()["turns"])

    def _prune_sessions_locked(self) -> None:
        while len(self._sessions) > self.max_sessions:
            sid, _ = self._sessions.popitem(last=False)
            self._delete_session_db(sid)

    def _delete_session_db(self, session_id: str) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute(f"DELETE FROM {self.TABLE} WHERE session_id = ?", (session_id,))
            self._conn.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("清理持久化会话失败：%s", type(exc).__name__)

    # ------------------------------------------------------------------
    def get(self, session_id: str) -> list[Turn]:
        with self._lock:
            turns = self._sessions.get(session_id)
            if not turns:
                return []
            self._sessions.move_to_end(session_id)   # 近期使用过的会话更不容易被淘汰
            return list(turns)

    def append_turn(self, session_id: str, question: str, answer: str,
                    intent: str | None, advertiser_id: str | None) -> int:
        """记一轮完整问答，返回该会话当前的轮数。"""
        with self._lock:
            turns = self._sessions.setdefault(session_id, [])
            turns.append(Turn(role="user", content=question[:500]))
            turns.append(Turn(role="assistant", content=answer[:self.answer_chars],
                              intent=intent, advertiser_id=advertiser_id))
            if len(turns) > self.max_turns:
                del turns[:len(turns) - self.max_turns]
            self._sessions.move_to_end(session_id)
            self._prune_sessions_locked()
            self._persist_session(session_id, turns)
            return len(turns)

    def _persist_session(self, session_id: str, turns: list[Turn]) -> None:
        """整会话重写。

        曾在增量删除上踩过坑：轮次裁剪后 seq 会前移，按 seq 删除会漏删/错删，
        重启后记忆条数和运行时对不上。会话轮数本来就有上限（默认 12），
        全量覆盖写又短又稳，没必要为了省几行写入引入状态不一致。
        """
        if self._conn is None:
            return
        try:
            self._conn.execute(
                f"DELETE FROM {self.TABLE} WHERE session_id = ?", (session_id,)
            )
            for i, turn in enumerate(turns):
                self._conn.execute(
                    f"INSERT INTO {self.TABLE} "
                    "(session_id, seq, role, content, intent, advertiser_id, ts) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (session_id, i, turn.role, turn.content, turn.intent,
                     turn.advertiser_id, turn.ts),
                )
            self._conn.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("对话记忆写入失败：%s", type(exc).__name__)

    def clear(self, session_id: str) -> bool:
        with self._lock:
            existed = session_id in self._sessions
            self._sessions.pop(session_id, None)
            self._delete_session_db(session_id)
            return existed

    def clear_all(self) -> int:
        with self._lock:
            n = len(self._sessions)
            self._sessions.clear()
            if self._conn is not None:
                try:
                    self._conn.execute(f"DELETE FROM {self.TABLE}")
                    self._conn.commit()
                except Exception as exc:  # noqa: BLE001
                    log.warning("清空持久化记忆失败：%s", type(exc).__name__)
            return n

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "sessions": len(self._sessions),
                "turns": sum(len(v) for v in self._sessions.values()),
                "max_turns": self.max_turns,
                "max_sessions": self.max_sessions,
                "persisted": bool(self._conn is not None),
            }

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:  # noqa: BLE001
                    pass
                self._conn = None


# ----------------------------------------------------------------------
def build_history_prompt(turns: list[Turn | dict], max_turns: int = 6,
                         max_chars: int = 900) -> str:
    """把最近几轮压成一段提示词，只在在线模型模式下注入。

    只放用户问题 + 回答摘要，不放证据/轨迹：上下文是给模型理解指代的，
    不是让它复述上一轮的数字——数字仍以本轮工具返回为准。
    既接受 Turn 对象，也接受 agent 传过来的 dict。
    """
    if not turns:
        return ""
    picked = turns[-max_turns * 2:]
    lines: list[str] = []
    used = 0
    for t in reversed(picked):
        role = t["role"] if isinstance(t, dict) else t.role
        intent = (t.get("intent") if isinstance(t, dict) else t.intent) or "-"
        content = t["content"] if isinstance(t, dict) else t.content
        piece = f"- {role}（{intent}）：{content[:140]}"
        if used + len(piece) > max_chars:
            # 跳过这条过长的，继续收更短的早期轮次；break 会让一条长回答
            # 挡住所有更早的上下文，追问时丢失关键背景。
            continue
        lines.append(piece)
        used += len(piece)
    lines.reverse()
    return "\n".join(lines)


def turns_to_json(turns: list[Turn]) -> list[dict[str, Any]]:
    return [t.to_dict() for t in turns]


def json_to_turns(rows: list[dict[str, Any]]) -> list[Turn]:
    return [Turn(role=r["role"], content=r["content"], intent=r.get("intent"),
                 advertiser_id=r.get("advertiser_id"), ts=float(r.get("ts", time.time())))
            for r in rows]
