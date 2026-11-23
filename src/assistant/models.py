"""核心数据模型：Event（主观）与 Metric（客观）。

Phase 0 只定义模型，不实现存储。Phase 1B / Phase 2 分别实现落库。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

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
    """客观运动/健康指标（存 MetricStore）。

    一条 Metric = 一条结构化客观数据。kind 区分类型，payload 存佳明原始返回，
    source_id 是佳明侧原始 ID（活动用 activityId，日指标用日期）。
    时间锚点用 start_time / end_time，供 TimeAligner 与主观事件对齐。
    """

    id: str
    kind: str
    """类型：activity | sleep | daily | hrv | stress | body_battery | rhr |
    weight | respiration | spo2 | training_readiness | training_status |
    vo2max | calories | hydration | intensity 等，可扩展。"""

    start_time: datetime
    end_time: datetime | None = None
    source_id: str | None = None      # 佳明原始 ID（activityId / 日期）
    source: str = "garmin"
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def make_id(cls, kind: str, source_id: str | None) -> str:
        """生成稳定 ID，便于 upsert 去重与增量同步。"""
        return f"{kind}:{source_id}" if source_id else f"{kind}:{uuid4().hex}"

    @classmethod
    def from_garmin(
        cls,
        kind: str,
        source_id: str | None,
        start_time: datetime,
        payload: dict[str, Any],
        *,
        end_time: datetime | None = None,
        source: str = "garmin",
    ) -> "Metric":
        """从佳明返回构造 Metric，自动填 id 与来源。"""
        return cls(
            id=cls.make_id(kind, source_id),
            kind=kind,
            start_time=start_time,
            end_time=end_time,
            source_id=source_id,
            source=source,
            payload=payload,
        )



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
