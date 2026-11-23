"""指标查询工具（Phase 2）：把客观数据检索注册成 Agent 工具。

用户问"上周跑了多少""最近睡得怎么样""跑10公里那天感觉如何"时，
Agent 调用这些工具查 MetricStore / TimeAligner 并据此回答。

注意：客观数据只在这里查，不生成事件、不写回 EventStore。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from assistant.alignment.sqlite_aligner import SQLiteTimeAligner, _metric_summary
from assistant.metrics.sqlite_store import SQLiteMetricStore
from assistant.recall.tools import _resolve_range
from assistant.tools import ToolRegistry


def _fmt_metrics(metrics: list) -> str:
    if not metrics:
        return "没有找到相关指标数据。"
    lines: list[str] = []
    for m in metrics:
        when = m.start_time.strftime("%Y-%m-%d %H:%M")
        lines.append(f"- [{when}] {m.kind}：{_metric_summary(m)}")
    return "\n".join(lines)


def register_metrics_tools(
    registry: ToolRegistry,
    store: SQLiteMetricStore,
    aligner: SQLiteTimeAligner | None = None,
) -> None:
    """把指标查询 + 时间对齐工具注册进注册表。"""

    def query_activities(time_hint: str = "上周", limit: int = 20) -> str:
        """查询运动活动记录（跑步/骑行等），含距离、时长、类型。time_hint 可填'今天/上周/本月/N天'。"""
        now = datetime.now().astimezone()
        start, end = _resolve_range(time_hint, now)
        items = store.query_range(start, end, kinds=["activity"], limit=limit)
        return _fmt_metrics(items)

    def query_sleep(time_hint: str = "上周", limit: int = 10) -> str:
        """查询睡眠数据。time_hint 可填'昨天/上周/本月/N天'。"""
        now = datetime.now().astimezone()
        start, end = _resolve_range(time_hint, now)
        items = store.query_range(start, end, kinds=["sleep"], limit=limit)
        return _fmt_metrics(items)

    def query_metric(kind: str, time_hint: str = "上周", limit: int = 15) -> str:
        """按类型查询任意客观指标。kind 填 activity/sleep/daily/steps/hrv/rhr/heart_rate/stress/body_battery/weight/vo2max 等；time_hint 可填时间范围。"""
        now = datetime.now().astimezone()
        start, end = _resolve_range(time_hint, now)
        items = store.query_range(start, end, kinds=[kind], limit=limit)
        return _fmt_metrics(items)

    def metrics_overview() -> str:
        """查看客观指标库的概况：各类型条数、数据时间跨度、同步游标。"""
        s = store.stats()
        by_kind = "、".join(f"{k} {v}" for k, v in s["by_kind"].items()) or "无"
        parts = [f"客观指标共 {s['total']} 条", f"分类：{by_kind}"]
        if s["oldest"] and s["newest"]:
            parts.append(f"时间跨度：{s['oldest'][:10]} ~ {s['newest'][:10]}")
        if s["cursor"]:
            parts.append(f"同步至：{s['cursor'][:10]}")
        return "；".join(parts)

    registry.register(query_activities, description=query_activities.__doc__)
    registry.register(query_sleep, description=query_sleep.__doc__)
    registry.register(query_metric, description=query_metric.__doc__)
    registry.register(metrics_overview, description=metrics_overview.__doc__)

    if aligner is None:
        return

    def recall_feeling_of_metric(metric_query: str, window_hours: int = 24) -> str:
        """按客观指标回忆当时的主观感受。例：metric_query 填'跑步'或'activity'，问'那天的感受'。返回该指标时间窗内的生活记录。"""
        pairs = aligner.align_by_metric(metric_query, window_hours)
        return SQLiteTimeAligner.format_metric_anchors(pairs, window_hours)

    def recall_metric_of_feeling(event_query: str, window_hours: int = 24) -> str:
        """按主观感受查当时的客观指标。例：event_query 填'很累'或'疲惫'，返回那段时间窗的运动/睡眠数据。"""
        pairs = aligner.align_by_event(event_query, window_hours)
        return SQLiteTimeAligner.format_event_anchors(pairs, window_hours)

    registry.register(
        recall_feeling_of_metric, description=recall_feeling_of_metric.__doc__
    )
    registry.register(
        recall_metric_of_feeling, description=recall_metric_of_feeling.__doc__
    )
