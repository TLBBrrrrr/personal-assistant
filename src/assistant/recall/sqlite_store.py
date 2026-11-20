"""SQLiteEventStore：EventStore 协议的 SQLite 实现（Phase 1B）。

设计要点：
- 同步核心方法（add/find/...）供 CLI、hook、工具直接调用；async 方法是对协议的兼容封装。
- events 表存结构化字段，events_fts 虚表做全文检索，tags/entities 以 JSON 存储并在内存中过滤。
- 中文检索以 LIKE 为主、FTS5 为辅（unicode61 对中文分词弱），两路结果合并去重。
- 数据落在 data_dir/events.db，与客观指标库物理分离（Phase 2 再建 metrics.db）。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncIterator

from assistant.models import Event

_JSON_FIELDS = ("tags", "entities", "metric_refs")


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(dt: datetime) -> str:
    # naive 视为本地时区，保证与库内 aware 时间字符串可比
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.isoformat(timespec="microseconds")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SQLiteEventStore:
    """基于 SQLite 的事件日志存储。线程内同步使用；async 方法仅做协议兼容。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._fts_ok = True
        self._init_db()

    # ---------- 初始化 ----------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id            TEXT PRIMARY KEY,
                    occurred_at   TEXT NOT NULL,
                    recorded_at   TEXT NOT NULL,
                    source        TEXT NOT NULL,
                    type          TEXT NOT NULL,
                    title         TEXT NOT NULL,
                    content       TEXT NOT NULL,
                    tags          TEXT NOT NULL,
                    entities      TEXT NOT NULL,
                    mood          TEXT,
                    importance    REAL NOT NULL,
                    metric_refs   TEXT NOT NULL,
                    embedding_ref TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_occurred ON events(occurred_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)"
            )
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
                        event_id UNINDEXED, title, content, tags_text
                    )
                    """
                )
            except sqlite3.Error:
                # 某些 SQLite 构建不含 FTS5，降级为纯 LIKE 检索
                self._fts_ok = False

    # ---------- 行 <-> 模型 ----------
    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        data = dict(row)
        for key in _JSON_FIELDS:
            data[key] = json.loads(data[key] or "[]")
        data["occurred_at"] = _parse(data["occurred_at"])
        data["recorded_at"] = _parse(data["recorded_at"])
        return Event(**data)

    @staticmethod
    def _event_to_params(event: Event) -> tuple:
        return (
            event.id,
            _iso(event.occurred_at),
            _iso(event.recorded_at),
            event.source,
            event.type,
            event.title,
            event.content,
            json.dumps(event.tags, ensure_ascii=False),
            json.dumps(event.entities, ensure_ascii=False),
            event.mood,
            event.importance,
            json.dumps(event.metric_refs, ensure_ascii=False),
            event.embedding_ref,
        )

    def _fts_insert(self, conn: sqlite3.Connection, event: Event) -> None:
        if not self._fts_ok:
            return
        conn.execute(
            "INSERT INTO events_fts(event_id, title, content, tags_text) VALUES (?,?,?,?)",
            (event.id, event.title, event.content, " ".join(event.tags)),
        )

    # ---------- 同步核心 API ----------
    def add(self, event: Event) -> str:
        """写入一条事件，返回 id。id 为空时自动生成。"""
        if not event.id:
            event.id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events (id, occurred_at, recorded_at, source, type, title,
                                    content, tags, entities, mood, importance,
                                    metric_refs, embedding_ref)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                self._event_to_params(event),
            )
            self._fts_insert(conn, event)
        return event.id

    def add_many(self, events: list[Event]) -> list[str]:
        return [self.add(e) for e in events]

    def get(self, event_id: str) -> Event | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM events WHERE id=?", (event_id,)
            ).fetchone()
        return self._row_to_event(row) if row else None

    def update(self, event_id: str, patch: dict) -> bool:
        current = self.get(event_id)
        if current is None:
            return False
        allowed = {
            "title", "content", "type", "tags", "entities", "mood",
            "importance", "occurred_at", "metric_refs", "source",
        }
        for key, value in patch.items():
            if key in allowed:
                setattr(current, key, value)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE events SET occurred_at=?, recorded_at=?, source=?, type=?, title=?,
                    content=?, tags=?, entities=?, mood=?, importance=?, metric_refs=?,
                    embedding_ref=? WHERE id=?
                """,
                self._event_to_params(current)[1:] + (event_id,),
            )
            if self._fts_ok:
                conn.execute(
                    "DELETE FROM events_fts WHERE event_id=?", (event_id,)
                )
                self._fts_insert(conn, current)
        return True

    def delete(self, event_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM events WHERE id=?", (event_id,))
            if self._fts_ok:
                conn.execute("DELETE FROM events_fts WHERE event_id=?", (event_id,))
        return cur.rowcount > 0

    @staticmethod
    def _aware(dt: datetime) -> datetime:
        """naive datetime 按本地时区补上 tzinfo，避免与 aware 比较报错。"""
        return dt if dt.tzinfo is not None else dt.astimezone()

    @classmethod
    def _passes(cls, event: Event, time_range, tags) -> bool:
        if time_range:
            start, end = cls._aware(time_range[0]), cls._aware(time_range[1])
            occurred = cls._aware(event.occurred_at)
            if not (start <= occurred <= end):
                return False
        if tags:
            lowered = {t.lower() for t in tags}
            if not lowered.intersection({t.lower() for t in event.tags}):
                return False
        return True

    def find(
        self,
        query: str = "",
        time_range: tuple[datetime, datetime] | None = None,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[Event]:
        """关键词检索：LIKE 为主 + FTS5 补充，合并去重后按时间倒序。"""
        rows: dict[str, sqlite3.Row] = {}
        keyword = (query or "").strip()

        with self._connect() as conn:
            if keyword:
                like = f"%{keyword}%"
                for row in conn.execute(
                    """
                    SELECT * FROM events
                    WHERE title LIKE ? OR content LIKE ? OR tags LIKE ? OR entities LIKE ?
                    ORDER BY occurred_at DESC LIMIT ?
                    """,
                    (like, like, like, like, limit * 3),
                ):
                    rows[row["id"]] = row

                if self._fts_ok:
                    safe = keyword.replace('"', " ")
                    fts_q = f'"{safe}"' if safe.strip() else ""
                    if fts_q:
                        try:
                            for row in conn.execute(
                                """
                                SELECT e.* FROM events_fts f
                                JOIN events e ON e.id = f.event_id
                                WHERE events_fts MATCH ?
                                ORDER BY e.occurred_at DESC LIMIT ?
                                """,
                                (fts_q, limit * 3),
                            ):
                                rows.setdefault(row["id"], row)
                        except sqlite3.Error:
                            pass
            else:
                for row in conn.execute(
                    "SELECT * FROM events ORDER BY occurred_at DESC LIMIT ?",
                    (limit * 3,),
                ):
                    rows[row["id"]] = row

        results = [
            self._row_to_event(row)
            for row in rows.values()
            if self._passes(self._row_to_event(row), time_range, tags)
        ]
        results.sort(key=lambda e: e.occurred_at, reverse=True)
        return results[:limit]

    def by_tag(
        self, topic: str, time_range: tuple[datetime, datetime] | None = None
    ) -> list[Event]:
        """按主题回顾：匹配 tags/entities，并补充正文关键词。"""
        like = f"%{topic}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE tags LIKE ? OR entities LIKE ? OR title LIKE ? OR content LIKE ?
                ORDER BY occurred_at DESC LIMIT 100
                """,
                (like, like, like, like),
            ).fetchall()
        events = [self._row_to_event(r) for r in rows]
        return [e for e in events if self._passes(e, time_range, None)]

    def timeline(
        self,
        start: datetime,
        end: datetime,
        page: int = 0,
        size: int = 50,
    ) -> list[Event]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE occurred_at >= ? AND occurred_at <= ?
                ORDER BY occurred_at DESC LIMIT ? OFFSET ?
                """,
                (_iso(start), _iso(end), size, page * size),
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            by_type = {
                r["type"]: r["n"]
                for r in conn.execute(
                    "SELECT type, COUNT(*) AS n FROM events GROUP BY type"
                )
            }
            by_mood = {
                (r["mood"] or "未标记"): r["n"]
                for r in conn.execute(
                    "SELECT mood, COUNT(*) AS n FROM events GROUP BY mood"
                )
            }
        return {"total": total, "by_type": by_type, "by_mood": by_mood}

    def similar_exists(self, title: str, within_hours: int = 24) -> bool:
        """简单去重：同标题在时间窗内已存在则视为重复。"""
        since = _iso(_now() - timedelta(hours=within_hours))
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM events WHERE title=? AND occurred_at>=? LIMIT 1",
                (title, since),
            ).fetchone()
        return row is not None

    # ---------- async Protocol 兼容封装 ----------
    async def append(self, event: Event) -> str:
        return self.add(event)

    async def append_many(self, events: list[Event]) -> list[str]:
        return self.add_many(events)

    async def update_async(self, event_id: str, patch: dict) -> None:
        self.update(event_id, patch)

    async def search(
        self,
        query: str,
        time_range: tuple[datetime, datetime] | None = None,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[Event]:
        return self.find(query, time_range, tags, limit)

    async def by_topic(
        self, topic: str, time_range: tuple[datetime, datetime] | None = None
    ) -> list[Event]:
        return self.by_tag(topic, time_range)

    async def timeline_async(
        self, start: datetime, end: datetime, page: int = 0, size: int = 50
    ) -> list[Event]:
        return self.timeline(start, end, page, size)

    async def iter_range(
        self, start: datetime, end: datetime
    ) -> AsyncIterator[Event]:
        for event in self.timeline(start, end, page=0, size=10_000):
            yield event

    async def delete_async(self, event_id: str) -> None:
        self.delete(event_id)

    async def stats_async(self) -> dict:
        return self.stats()
