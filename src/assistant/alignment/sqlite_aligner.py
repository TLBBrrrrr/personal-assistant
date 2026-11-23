"""SQLiteTimeAligner：TimeAligner 协议的实现。

按时间窗桥接 MetricStore（客观）与 EventStore（主观）。
对齐靠时间窗，不靠内容相似度——两类数据没有语义可比性。

两个方向：
- align_by_metric：先找指标锚点（如 activity 且含关键词），再取同窗口事件。
- align_by_event：先找事件锚点（关键词/主题），再取同窗口指标。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from assistant.alignment.aligner import TimeAligner
from assistant.models import Event, Metric


class SQLiteTimeAligner(TimeAligner):
    """基于时间窗的跨存储对齐。同步方法为主，async 做协议兼容。"""

    def __init__(self, metric_store: Any, event_store: Any) -> None:
        self.metrics = metric_store
        self.events = event_store

    # ---------- 锚点查找 ----------
    def _find_metrics(self, query: str, limit: int = 5) -> list[Metric]:
        """把查询串解析成指标锚点：命中 kind 就按 kind 取最新，否则扫 payload 关键词。"""
        q = (query or "").strip()
        if not q:
            latest = self.metrics.latest("activity")
            return [latest] if latest else []

        # 1) 精确命中 kind：如 "activity" / "sleep"
        known = self.metrics.kinds()
        if q.lower() in known:
            items = self.metrics.query_range(
                datetime.min, datetime.max, kinds=[q.lower()], limit=limit
            )
            return items

        # 2) 关键词：在 payload JSON 文本里做包含匹配
        hits: list[Metric] = []
        all_metrics = self.metrics.query_range(datetime.min, datetime.max, limit=5000)
        for m in all_metrics:
            blob = json.dumps(m.payload, ensure_ascii=False)
            if q in blob or q.lower() in blob.lower():
                hits.append(m)
            if len(hits) >= limit:
                break
        return hits

    def _find_events(self, query: str, limit: int = 5) -> list[Event]:
        q = (query or "").strip()
        if not q:
            return self.events.find("", limit=limit)
        # 主题/关键词两路合并：by_tag 覆盖主题，find 覆盖正文
        seen: dict[str, Event] = {}
        for e in self.events.by_tag(q):
            seen[e.id] = e
        for e in self.events.find(q, limit=limit):
            seen.setdefault(e.id, e)
        return sorted(seen.values(), key=lambda e: e.occurred_at, reverse=True)[:limit]

    # ---------- 对齐 ----------
    def align_by_metric(
        self, metric_query: str, window_hours: int = 24
    ) -> list[tuple[Metric, list[Event]]]:
        """先查指标锚点，再取同窗口事件。例：跑10公里那天的感受。"""
        results: list[tuple[Metric, list[Event]]] = []
        for metric in self._find_metrics(metric_query):
            center = metric.start_time
            start = center - timedelta(hours=window_hours)
            end = (metric.end_time or center) + timedelta(hours=window_hours)
            events = self.events.find("", time_range=(start, end), limit=50)
            results.append((metric, events))
        return results

    def align_by_event(
        self, event_query: str, window_hours: int = 24
    ) -> list[tuple[Event, list[Metric]]]:
        """先查事件锚点，再取同窗口指标。例：疲惫那天的睡眠。"""
        results: list[tuple[Event, list[Metric]]] = []
        for event in self._find_events(event_query):
            center = event.occurred_at
            start = center - timedelta(hours=window_hours)
            end = center + timedelta(hours=window_hours)
            metrics = self.metrics.query_range(start, end, limit=50)
            results.append((event, metrics))
        return results

    # ---------- 格式化（供工具直接展示） ----------
    @staticmethod
    def format_metric_anchors(
        pairs: list[tuple[Metric, list[Event]]], window_hours: int
    ) -> str:
        if not pairs:
            return "没有找到匹配的客观指标。"
        lines: list[str] = []
        for metric, events in pairs:
            when = metric.start_time.strftime("%Y-%m-%d %H:%M")
            summary = _metric_summary(metric)
            lines.append(f"- [{when}] 指标（{metric.kind}）：{summary}")
            if events:
                for e in events[:5]:
                    ew = e.occurred_at.strftime("%m-%d %H:%M")
                    lines.append(f"    · [{ew}] {e.title}：{e.content[:60]}")
            else:
                lines.append(f"    · 前后 {window_hours} 小时没有相关记录。")
        return "\n".join(lines)

    @staticmethod
    def format_event_anchors(
        pairs: list[tuple[Event, list[Metric]]], window_hours: int
    ) -> str:
        if not pairs:
            return "没有找到匹配的生活记录。"
        lines: list[str] = []
        for event, metrics in pairs:
            when = event.occurred_at.strftime("%Y-%m-%d %H:%M")
            lines.append(f"- [{when}] {event.title}：{event.content[:60]}")
            if metrics:
                for m in metrics[:6]:
                    mw = m.start_time.strftime("%m-%d %H:%M")
                    lines.append(f"    · [{mw}] {m.kind}：{_metric_summary(m)}")
            else:
                lines.append(f"    · 前后 {window_hours} 小时没有客观指标。")
        return "\n".join(lines)

    # ---------- async Protocol 兼容 ----------
    async def align_by_metric_async(
        self, metric_query: str, window_hours: int = 24
    ) -> list[tuple[Metric, list[Event]]]:
        return self.align_by_metric(metric_query, window_hours)

    async def align_by_event_async(
        self, event_query: str, window_hours: int = 24
    ) -> list[tuple[Event, list[Metric]]]:
        return self.align_by_event(event_query, window_hours)


def _metric_summary(metric: Metric) -> str:
    """给指标生成一行可读摘要，优先取已提取的顶层字段。"""
    p = metric.payload
    kind = metric.kind
    if kind == "activity":
        distance = p.get("_distance_km")
        duration = p.get("_duration_min")
        typ = p.get("_activity_type") or "运动"
        name = p.get("activityName") or ""
        parts = [str(typ)]
        if name:
            parts.append(name)
        if distance:
            parts.append(f"{distance} 公里")
        if duration:
            parts.append(f"{duration} 分钟")
        return " · ".join(parts)
    # 其他类别：取 payload 里的常见汇总字段
    for key in ("totalSteps", "steps", "restingHeartRate", "averageHR",
                "sleepScore", "overallScore", "averageStressLevel", "value"):
        if key in p and p[key] is not None:
            return f"{key}={p[key]}"
    # 兜底：列出前几个标量字段
    scalars = [
        f"{k}={v}"
        for k, v in p.items()
        if isinstance(v, (int, float, str)) and not k.startswith("_")
    ][:4]
    return "、".join(scalars) if scalars else "（无摘要字段）"
