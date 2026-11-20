"""recall 工具（Phase 1B）：把事件检索注册成 Agent 工具。

用户对话里直接问"上个月我做了什么""关于跑步我都记了什么"，
Agent 就能调用这些工具查 EventStore 并据此回答。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from assistant.recall.sqlite_store import SQLiteEventStore
from assistant.tools import ToolRegistry


def _fmt(events: list) -> str:
    if not events:
        return "没有找到相关记录。"
    lines = []
    for e in events:
        when = e.occurred_at.strftime("%Y-%m-%d %H:%M")
        tags = "、".join(e.tags) if e.tags else "无标签"
        mood = f"；心情：{e.mood}" if e.mood else ""
        lines.append(f"- [{when}] {e.title}（{tags}{mood}）\n  {e.content}")
    return "\n".join(lines)


def _resolve_range(time_hint: str, now: datetime) -> tuple[datetime, datetime]:
    """把'今天/昨天/上周/本月/上个月/N天'解析为时间窗，失败则返回近一年。"""
    end = now
    hint = (time_hint or "").strip()
    if "今天" in hint or "今日" in hint:
        return now.replace(hour=0, minute=0, second=0, microsecond=0), end
    if "昨天" in hint or "昨日" in hint:
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    if "上周" in hint or "上星期" in hint:
        this_monday = now - timedelta(days=now.weekday())
        return this_monday - timedelta(weeks=1), this_monday
    if "这周" in hint or "本周" in hint or "本周" in hint:
        return now - timedelta(days=now.weekday()), end
    if "上个月" in hint or "上月" in hint:
        first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_end = first_this - timedelta(seconds=1)
        prev_start = (prev_end.replace(day=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return prev_start, prev_end
    if "本月" in hint or "这个月" in hint:
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), end
    if "去年" in hint:
        return now.replace(year=now.year - 1, month=1, day=1), now.replace(
            year=now.year - 1, month=12, day=31, hour=23, minute=59
        )
    if "今年" in hint:
        return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0), end
    import re

    m = re.search(r"(\d+)天", hint)
    if m:
        return now - timedelta(days=int(m.group(1))), end
    # 没识别到时间词：给一个较宽的窗口，避免漏掉
    return now - timedelta(days=365), end


def register_recall_tools(registry: ToolRegistry, store: SQLiteEventStore) -> None:
    """把 recall 系列工具注册进注册表。"""

    def recall_search(query: str, time_hint: str = "", limit: int = 10) -> str:
        """按关键词回忆生活记录。query 是要找的内容；time_hint 可填'昨天/上周/上个月'等时间范围，留空表示不限；limit 是最多返回条数。"""
        now = datetime.now().astimezone()
        if time_hint:
            start, end = _resolve_range(time_hint, now)
            events = store.find(query, time_range=(start, end), limit=limit)
        else:
            events = store.find(query, limit=limit)
        return _fmt(events)

    def recall_topic(topic: str, time_hint: str = "") -> str:
        """按主题回顾记录，比如'跑步''工作''张三'。topic 是主题关键词，time_hint 是可选时间范围。"""
        now = datetime.now().astimezone()
        if time_hint:
            start, end = _resolve_range(time_hint, now)
            events = store.by_tag(topic, time_range=(start, end))
        else:
            events = store.by_tag(topic)
        return _fmt(events[:20])

    def recall_timeline(time_hint: str = "本周", page: int = 0, size: int = 30) -> str:
        """按时间线浏览记录，像翻日记。time_hint 填'今天/昨天/上周/本月/上个月'，page 从 0 开始。"""
        now = datetime.now().astimezone()
        start, end = _resolve_range(time_hint, now)
        events = store.timeline(start, end, page=page, size=size)
        if not events:
            return f"{time_hint or '该时段'}没有记录。"
        return f"{time_hint or '该时段'}的记录（第 {page + 1} 页）：\n" + _fmt(events)

    def recall_stats() -> str:
        """查看已记录了多少条生活事件、按类型和心情的分布。"""
        s = store.stats()
        types = "、".join(f"{k} {v}" for k, v in s["by_type"].items()) or "无"
        moods = "、".join(f"{k} {v}" for k, v in s["by_mood"].items()) or "无"
        return f"共记录 {s['total']} 条事件。类型：{types}。心情：{moods}。"

    registry.register(recall_search, description=recall_search.__doc__)
    registry.register(recall_topic, description=recall_topic.__doc__)
    registry.register(recall_timeline, description=recall_timeline.__doc__)
    registry.register(recall_stats, description=recall_stats.__doc__)
