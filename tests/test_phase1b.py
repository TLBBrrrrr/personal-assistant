"""Phase 1B 测试：EventStore 持久化、时间解析、记录 hook、recall 工具。

全程离线：不消耗模型额度。抽取判定用直接构造 Event 验证落库链路，
模型结构化输出能力另由 extractor 的 TestModel 冒烟测试覆盖。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from assistant.models import Event
from assistant.recall import SQLiteEventStore, resolve_when
from assistant.recall.extractor import StaticEventExtractor, ExtractionResult
from assistant.recall.tools import register_recall_tools
from assistant.tools import ToolRegistry


def make_event(title="跑了10公里很畅快", **kw) -> Event:
    now = datetime.now().astimezone()
    defaults = dict(
        id="",
        occurred_at=now - timedelta(days=1),
        recorded_at=now,
        source="dialogue",
        type="experience",
        title=title,
        content="昨天去跑步10公里，最后两公里冲刺，感觉特别爽",
        tags=["跑步", "心情"],
        mood="开心",
        importance=0.8,
    )
    defaults.update(kw)
    return Event(**defaults)


@pytest.fixture
def store(tmp_path):
    return SQLiteEventStore(tmp_path / "events.db")


# ---------- 时间解析 ----------
def test_resolve_when_relative():
    now = datetime(2026, 11, 20, 10, 0)
    assert resolve_when("昨天", now).date().isoformat() == "2026-11-19"
    assert resolve_when("前天", now).date().isoformat() == "2026-11-18"
    assert resolve_when("3天前", now).date().isoformat() == "2026-11-17"
    assert resolve_when("今天", now) == now
    assert resolve_when("", now) == now


def test_resolve_when_unknown_defaults_now():
    now = datetime(2026, 11, 20, 10, 0)
    assert resolve_when("很久以前某天", now) == now


# ---------- EventStore ----------
def test_add_and_get(store):
    eid = store.add(make_event())
    assert len(eid) == 32
    got = store.get(eid)
    assert got is not None
    assert got.title == "跑了10公里很畅快"
    assert got.tags == ["跑步", "心情"]


def test_keyword_search(store):
    store.add(make_event())
    store.add(make_event(
        title="开会讨论项目",
        content="下午开了两个小时的项目进度会",
        tags=["工作"],
    ))

    assert len(store.find("跑步")) == 1
    assert len(store.find("冲刺")) == 1
    assert len(store.find("不存在的词")) == 0


def test_by_topic(store):
    store.add(make_event())
    store.add(make_event(
        title="和张三吃饭",
        content="晚上和张三在楼下吃了火锅",
        tags=["人际"],
        entities=["张三"],
    ))
    assert len(store.by_tag("跑步")) == 1
    assert len(store.by_tag("张三")) == 1


def test_time_range_and_timeline(store):
    store.add(make_event())
    now = datetime.now()
    assert len(store.find("", time_range=(now - timedelta(days=2), now + timedelta(days=1)))) == 1
    assert len(store.timeline(now - timedelta(days=2), now + timedelta(days=1))) == 1
    assert len(store.timeline(now - timedelta(days=10), now - timedelta(days=5))) == 0


def test_update_delete_stats(store):
    eid = store.add(make_event())
    assert store.stats()["total"] == 1
    assert store.update(eid, {"mood": "平静"}) is True
    assert store.get(eid).mood == "平静"
    assert store.delete(eid) is True
    assert store.count() == 0


def test_persistence_across_reopen(tmp_path):
    """关键：关掉再打开，事件还在——这是 Phase 0 完全没有的能力。"""
    db = tmp_path / "events.db"
    s1 = SQLiteEventStore(db)
    s1.add(make_event(title="跨会话保留的事件"))
    del s1

    s2 = SQLiteEventStore(db)
    found = s2.find("跨会话")
    assert len(found) == 1
    assert found[0].title == "跨会话保留的事件"


# ---------- 记录 hook ----------
def test_static_extractor_records(store):
    extractor = StaticEventExtractor(store)
    events = extractor.extract("昨天去爬山了，特别开心")
    assert len(events) == 1
    assert store.count() == 1
    assert store.find("爬山")


def test_recall_tools_registered(store):
    registry = ToolRegistry()
    register_recall_tools(registry, store)
    names = registry.names()
    assert "recall_search" in names
    assert "recall_topic" in names
    assert "recall_timeline" in names
    assert "recall_stats" in names

    store.add(make_event(title="看了一场电影", tags=["娱乐"]))
    out = registry.invoke("recall_search", query="电影")
    assert "看了一场电影" in out
    topic = registry.invoke("recall_topic", topic="娱乐")
    assert "看了一场电影" in topic
    stats = registry.invoke("recall_stats")
    assert "1 条事件" in stats


# ---------- 抽取器结构化输出冒烟 ----------
def test_extractor_structured_output_with_test_model():
    """验证结构化输出管线在 TestModel 下可运行（不校验语义，只校验类型）。"""
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    agent = Agent(TestModel(), output_type=ExtractionResult, instructions="test")
    result = agent.run_sync("你好")
    assert isinstance(result.output, ExtractionResult)
