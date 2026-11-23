"""SQLiteMetricStore：MetricStore 协议的 SQLite 实现（Phase 2）。

设计要点：
- 单张 metrics 表存所有客观指标，用 kind 区分类型；payload 保留佳明原始 JSON。
- (kind, source_id) 唯一 → upsert 幂等，重复同步不产生重复行。
- 同步方法（upsert/query/...）供同步器与工具直接调用；async 方法做协议兼容封装。
- 同步游标存 meta 表，支持增量同步。
- 落在独立的 metrics.db，与 events.db 物理分离（客观/主观分库）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from assistant.models import Metric


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(dt: datetime) -> str:
    # naive 视为本地时区，用 replace 而非 astimezone：避免 datetime.min/max 边界溢出
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_local_tz())
    return dt.isoformat(timespec="microseconds")


def _local_tz():
    return datetime.now().astimezone().tzinfo


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.astimezone()


class SQLiteMetricStore:
    """基于 SQLite 的客观指标存储。同步方法为主；async 仅做协议兼容。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS metrics (
                    id          TEXT PRIMARY KEY,
                    kind        TEXT NOT NULL,
                    source_id   TEXT NOT NULL,
                    source      TEXT NOT NULL,
                    start_time  TEXT NOT NULL,
                    end_time    TEXT,
                    payload     TEXT NOT NULL,
                    synced_at   TEXT NOT NULL,
                    UNIQUE(kind, source_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_metrics_kind ON metrics(kind)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_metrics_start ON metrics(start_time)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_metrics_kind_start "
                "ON metrics(kind, start_time)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )

    # ---------- 行 <-> 模型 ----------
    @staticmethod
    def _row_to_metric(row: sqlite3.Row) -> Metric:
        data = dict(row)
        data["payload"] = json.loads(data["payload"] or "{}")
        data["start_time"] = _parse(data["start_time"])
        if data.get("end_time"):
            data["end_time"] = _parse(data["end_time"])
        # synced_at 不属于 Metric 模型，丢弃
        data.pop("synced_at", None)
        return Metric(**data)

    # ---------- 同步核心 API ----------
    def upsert(self, metric: Metric) -> str:
        """写入一条指标；(kind, source_id) 冲突时覆盖更新，返回 id。"""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO metrics
                    (id, kind, source_id, source, start_time, end_time, payload, synced_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(kind, source_id) DO UPDATE SET
                    id = excluded.id,
                    source = excluded.source,
                    start_time = excluded.start_time,
                    end_time = excluded.end_time,
                    payload = excluded.payload,
                    synced_at = excluded.synced_at
                """,
                (
                    metric.id,
                    metric.kind,
                    metric.source_id or "",
                    metric.source,
                    _iso(metric.start_time),
                    _iso(metric.end_time) if metric.end_time else None,
                    json.dumps(metric.payload, ensure_ascii=False, default=str),
                    _iso(_now()),
                ),
            )
        return metric.id

    def upsert_many(self, metrics: list[Metric]) -> int:
        """批量写入，返回本次条数。逐条 upsert，单条异常不影响其余。"""
        n = 0
        for m in metrics:
            try:
                self.upsert(m)
                n += 1
            except Exception:  # noqa: BLE001 - 同步边界：坏数据隔离
                continue
        return n

    def get(self, metric_id: str) -> Metric | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM metrics WHERE id=?", (metric_id,)
            ).fetchone()
        return self._row_to_metric(row) if row else None

    def query(
        self,
        kind: str,
        start: datetime,
        end: datetime,
        filters: dict[str, Any] | None = None,
    ) -> list[Metric]:
        """按 kind + 时间窗查询，可选对 payload 顶层字段做等值过滤。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM metrics
                WHERE kind=? AND start_time>=? AND start_time<=?
                ORDER BY start_time DESC
                """,
                (kind, _iso(start), _iso(end)),
            ).fetchall()
        metrics = [self._row_to_metric(r) for r in rows]
        if filters:
            metrics = [
                m
                for m in metrics
                if all(m.payload.get(k) == v for k, v in filters.items())
            ]
        return metrics

    def query_range(
        self,
        start: datetime,
        end: datetime,
        kinds: list[str] | None = None,
        limit: int = 200,
    ) -> list[Metric]:
        """跨 kind 的时间窗查询，用于 TimeAligner。"""
        sql = "SELECT * FROM metrics WHERE start_time>=? AND start_time<=?"
        params: list[Any] = [_iso(start), _iso(end)]
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            sql += f" AND kind IN ({placeholders})"
            params.extend(kinds)
        sql += " ORDER BY start_time DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_metric(r) for r in rows]

    def latest(self, kind: str) -> Metric | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM metrics WHERE kind=? ORDER BY start_time DESC LIMIT 1",
                (kind,),
            ).fetchone()
        return self._row_to_metric(row) if row else None

    def count(self, kind: str | None = None) -> int:
        with self._connect() as conn:
            if kind:
                return conn.execute(
                    "SELECT COUNT(*) FROM metrics WHERE kind=?", (kind,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]

    def kinds(self) -> dict[str, int]:
        with self._connect() as conn:
            return {
                r["kind"]: r["n"]
                for r in conn.execute(
                    "SELECT kind, COUNT(*) AS n FROM metrics GROUP BY kind ORDER BY n DESC"
                )
            }

    def stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
            by_kind = {
                r["kind"]: r["n"]
                for r in conn.execute(
                    "SELECT kind, COUNT(*) AS n FROM metrics GROUP BY kind ORDER BY n DESC"
                )
            }
            newest = conn.execute(
                "SELECT MAX(start_time) FROM metrics"
            ).fetchone()[0]
            oldest = conn.execute(
                "SELECT MIN(start_time) FROM metrics"
            ).fetchone()[0]
        return {
            "total": total,
            "by_kind": by_kind,
            "newest": newest,
            "oldest": oldest,
            "cursor": self.sync_cursor(),
        }

    # ---------- 同步游标 ----------
    def sync_cursor(self) -> datetime | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key='sync_cursor'"
            ).fetchone()
        return _parse(row["value"]) if row and row["value"] else None

    def set_sync_cursor(self, ts: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('sync_cursor', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (_iso(ts),),
            )

    def delete(self, metric_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM metrics WHERE id=?", (metric_id,))
        return cur.rowcount > 0

    # ---------- async Protocol 兼容封装 ----------
    async def upsert_async(self, metric: Metric) -> str:
        return self.upsert(metric)

    async def query_async(
        self,
        kind: str,
        start: datetime,
        end: datetime,
        filters: dict[str, Any] | None = None,
    ) -> list[Metric]:
        return self.query(kind, start, end, filters)

    async def latest_async(self, kind: str) -> Metric | None:
        return self.latest(kind)

    async def sync_cursor_async(self) -> datetime | None:
        return self.sync_cursor()

    async def set_sync_cursor_async(self, ts: datetime) -> None:
        self.set_sync_cursor(ts)
