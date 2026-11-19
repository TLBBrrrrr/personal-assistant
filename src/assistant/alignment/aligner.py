"""TimeAligner 协议：按时间窗桥接 MetricStore 与 EventStore。

Phase 0 只定义接口；Phase 3 实现。
对齐靠时间窗，不靠内容相似度（两类数据没有语义可比性）。
"""

from __future__ import annotations

from typing import Protocol

from assistant.models import Event, Metric


class TimeAligner(Protocol):
    """客观 ↔ 主观 的时间对齐查询协议。"""

    async def align_by_metric(
        self, metric_query: str, window_hours: int = 24
    ) -> list[tuple[Metric, list[Event]]]:
        """先查指标锚点，再取同窗口事件。例：跑10公里那天的感受。"""
        ...

    async def align_by_event(
        self, event_query: str, window_hours: int = 24
    ) -> list[tuple[Event, list[Metric]]]:
        """先查事件，再取同窗口指标。例：疲惫日子的睡眠。"""
        ...
