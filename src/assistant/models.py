"""核心数据模型：Event（主观）与 Metric（客观）。

Phase 0 只定义模型，不实现存储。Phase 1B / Phase 2 分别实现落库。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Event(BaseModel):
    """主观生活事件与感受（存 EventStore）。"""

    id: str
    occurred_at: datetime
    recorded_at: datetime
    source: str = "dialogue"          # dialogue | manual | digest | system
    type: str = "experience"          # experience | feeling | note | decision | milestone | summary
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    mood: str | None = None
    importance: float = 0.5
    metric_refs: list[str] = Field(default_factory=list)
    embedding_ref: str | None = None


class Metric(BaseModel):
    """客观运动/健康指标（存 MetricStore）。"""

    id: str
    kind: str                         # activity | sleep | daily
    start_time: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class Digest(BaseModel):
    """派生总结"""
    id: str
    period: str                       # week | month | year
    start: datetime
    end: datetime
    content: str
    event_ids: list[str] = Field(default_factory=list)
    metric_ids: list[str] = Field(default_factory=list)
    created_at: datetime
