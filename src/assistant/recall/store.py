"""EventStore 协议：主观生活事件的存储与检索。

Phase 0 只定义接口；Phase 1B 用 SQLite + FTS5 + 向量库实现。
"""

from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator, Protocol

from assistant.models import Event


class EventStore(Protocol):
    """事件日志存储协议（可替换实现）。"""

    # 写入
    async def append(self, event: Event) -> str: ...
    async def append_many(self, events: list[Event]) -> list[str]: ...
    async def update(self, event_id: str, patch: dict) -> None: ...

    # 检索
    async def search(
        self,
        query: str,
        time_range: tuple[datetime, datetime] | None = None,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[Event]: ...
    async def by_topic(
        self, topic: str, time_range: tuple[datetime, datetime] | None = None
    ) -> list[Event]: ...
    async def timeline(
        self, start: datetime, end: datetime, page: int = 0, size: int = 50
    ) -> list[Event]: ...
    async def iter_range(
        self, start: datetime, end: datetime
    ) -> AsyncIterator[Event]: ...

    # 维护
    async def delete(self, event_id: str) -> None: ...
    async def stats(self) -> dict: ...
