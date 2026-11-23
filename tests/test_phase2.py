"""Phase 2 测试：MetricStore、同步编排、时间对齐、工具注册。

用 mock Garmin 客户端验证同步映射，不连真实账号。
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta

import pytest

from assistant.alignment.sqlite_aligner import SQLiteTimeAligner
from assistant.metrics.sqlite_store import SQLiteMetricStore
from assistant.models import Event, Metric
from assistant.recall.sqlite_store import SQLiteEventStore
from assistant.sport import sync
from assistant.sport.client import GarminClient
from assistant.sport.sync import fetch_activities, fetch_sleep
from assistant.tools import ToolRegistry


@pytest.fixture
def metric_store(tmp_path):
    return SQLiteMetricStore(tmp_path / "metrics.db")


@pytest.fixture
def event_store(tmp_path):
    return SQLiteEventStore(tmp_path / "events.db")


# ---------- Metric 模型 ----------
def test_metric_make_id_stable():
    assert Metric.make_id("activity", "123") == "activity:123"
    assert Metric.make_id("activity", "123") == Metric.make_id("activity", "123")


def test_metric_from_garmin():
    t = datetime.now().astimezone()
    m = Metric.from_garmin("sleep", "2026-01-01", t, {"score": 80})
    assert m.id == "sleep:2026-01-01"
    assert m.source == "garmin"
    assert m.payload["score"] == 80


# ---------- MetricStore ----------
def test_upsert_and_idempotent(metric_store):
    t = datetime.now().astimezone()
    m = Metric.from_garmin("activity", "a1", t, {"distance": 1000})
    assert metric_store.upsert(m) == m.id
    # 同 (kind, source_id) 覆盖，不新增
    m2 = Metric.from_garmin("activity", "a1", t, {"distance": 2000})
    metric_store.upsert(m2)
    assert metric_store.count("activity") == 1
    assert metric_store.get(m.id).payload["distance"] == 2000


def test_query_range_and_kinds(metric_store):
    now = datetime.now().astimezone()
    metric_store.upsert(Metric.from_garmin("activity", "a1", now, {"d": 1}))
    metric_store.upsert(Metric.from_garmin("sleep", "s1", now, {"d": 2}))
    assert set(metric_store.kinds()) == {"activity", "sleep"}
    assert len(metric_store.query_range(now - timedelta(hours=1), now + timedelta(hours=1),
                                        kinds=["activity"])) == 1
    assert len(metric_store.query_range(now - timedelta(hours=1), now + timedelta(hours=1),
                                        kinds=["activity", "sleep"])) == 2


def test_sync_cursor(metric_store):
    assert metric_store.sync_cursor() is None
    t = datetime.now().astimezone()
    metric_store.set_sync_cursor(t)
    got = metric_store.sync_cursor()
    assert got is not None and got.date() == t.date()


def test_upsert_many_isolates_bad_data(metric_store):
    t = datetime.now().astimezone()
    good = Metric.from_garmin("activity", "a1", t, {})
    bad = Metric.from_garmin("activity", "a2", t, {"x": object()})  # 不可 JSON 序列化
    # default=str 兜底，故两条都应写入；这里验证不抛异常
    n = metric_store.upsert_many([good, bad])
    assert n == 2


# ---------- 抓取器（mock 客户端） ----------
class _FakeRaw:
    def get_activities_by_date(self, start, end):
        return [{
            "activityId": 999,
            "startTimeLocal": "2026-01-01 07:30:00",
            "distance": 10000,
            "elapsedDuration": 3600,
            "activityType": {"typeKey": "running"},
            "activityName": "晨跑",
        }]

    def get_sleep_data(self, day):
        return {"daySummary": {"sleepScore": 82}, "date": day}


class _FakeClient(GarminClient):
    def __init__(self):
        # 绕过真实连接，直接挂 raw
        self._client = _FakeRaw()
        self.token_dir = None


def test_fetch_activities_mapping():
    metrics = fetch_activities(_FakeClient(), "2026-01-01")
    assert len(metrics) == 1
    m = metrics[0]
    assert m.id == "activity:999"
    assert m.payload["_distance_km"] == 10.0
    assert m.payload["_duration_min"] == 60.0
    assert m.payload["_activity_type"] == "running"
    assert m.end_time is not None


def test_fetch_sleep_mapping():
    metrics = fetch_sleep(_FakeClient(), "2026-01-01")
    assert len(metrics) == 1
    assert metrics[0].id == "sleep:2026-01-01"
    assert metrics[0].payload["daySummary"]["sleepScore"] == 82


def test_sync_writes_metrics_and_cursor(metric_store):
    report = sync(_FakeClient(), metric_store, start_date="2026-01-01",
                  end_date="2026-01-01", kinds=["activity", "sleep"])
    assert metric_store.count("activity") == 1
    assert metric_store.count("sleep") == 1
    assert report.written["activity"] == 1
    assert report.written["sleep"] == 1
    assert metric_store.sync_cursor() is not None
    assert "同步完成" in report.summary()


def test_sync_isolates_fetcher_failure(metric_store):
    class BrokenRaw(_FakeRaw):
        def get_sleep_data(self, day):
            raise RuntimeError("boom")

    client = GarminClient.__new__(GarminClient)
    client._client = BrokenRaw()
    report = sync(client, metric_store, start_date="2026-01-01",
                  end_date="2026-01-01", kinds=["activity", "sleep"])
    assert metric_store.count("activity") == 1  # 好的类别照常写入
    assert any("sleep" in e for e in report.errors)


# ---------- 时间对齐 ----------
def test_align_by_metric(metric_store, event_store):
    t = datetime(2026, 1, 1, 8, 0, tzinfo=datetime.now().astimezone().tzinfo)
    metric_store.upsert(Metric.from_garmin("activity", "a1", t, {"activityName": "晨跑"}))
    event_store.add(Event(id="e1", occurred_at=t + timedelta(hours=1),
                          recorded_at=t, title="状态很好", content="跑完很爽", tags=["跑步"]))
    aligner = SQLiteTimeAligner(metric_store, event_store)
    pairs = aligner.align_by_metric("activity", window_hours=24)
    assert len(pairs) == 1 and len(pairs[0][1]) == 1
    out = aligner.format_metric_anchors(pairs, 24)
    assert "晨跑" in out and "状态很好" in out


def test_align_by_event(metric_store, event_store):
    t = datetime(2026, 1, 1, 8, 0, tzinfo=datetime.now().astimezone().tzinfo)
    event_store.add(Event(id="e1", occurred_at=t, recorded_at=t,
                          title="疲惫", content="今天很累", tags=["状态"]))
    metric_store.upsert(Metric.from_garmin("sleep", "s1", t, {"daySummary": {"sleepScore": 60}}))
    aligner = SQLiteTimeAligner(metric_store, event_store)
    pairs = aligner.align_by_event("疲惫", window_hours=24)
    assert len(pairs) == 1 and len(pairs[0][1]) == 1


# ---------- 工具注册 ----------
def test_register_metrics_tools(metric_store, event_store):
    from assistant.metrics.tools import register_metrics_tools

    registry = ToolRegistry()
    aligner = SQLiteTimeAligner(metric_store, event_store)
    register_metrics_tools(registry, metric_store, aligner)
    names = registry.names()
    for expected in ("query_activities", "query_sleep", "query_metric",
                     "metrics_overview", "recall_feeling_of_metric",
                     "recall_metric_of_feeling"):
        assert expected in names
    assert "没有找到" in registry.invoke("query_activities", time_hint="今天")
