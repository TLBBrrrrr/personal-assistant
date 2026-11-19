"""MetricStore 协议：客观运动数据的存储。

Phase 0 只定义接口；Phase 2 用 SQLite 业务表实现。
佳明同步只写这里，不进 EventStore、不做向量化。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from assistant.models import Metric


class MetricStore(Protocol):
    """客观指标存储协议（可替换实现）。"""

    async def upsert(self, metric: Metric) -> str: ...
    async def query(
        self,
        kind: str,
        start: datetime,
        end: datetime,
        filters: dict | None = None,
    ) -> list[Metric]: ...
    async def latest(self, kind: str) -> Metric | None: ...
    async def sync_cursor(self) -> datetime | None: ...
    async def set_sync_cursor(self, ts: datetime) -> None: ...
