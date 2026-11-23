"""Phase 2 测试：MetricStore、同步编排。

用 mock Garmin 客户端验证同步映射，不连真实账号。
只覆盖数据保存与查询核心，不含 Agent 工具 / 时间对齐。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from assistant.metrics.sqlite_store import SQLiteMetricStore
from assistant.models import Metric
from assistant.sport import sync
from assistant.sport.client import GarminClient
from assistant.sport.sync import fetch_activities, fetch_sleep


@pytest.fixture
def metric_store(tmp_path):
    return SQLiteMetricStore(tmp_path / "metrics.db")


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


def test_upsert_many_accepts_all(metric_store):
    t = datetime.now().astimezone()
    good = Metric.from_garmin("activity", "a1", t, {})
    with_obj = Metric.from_garmin("activity", "a2", t, {"x": object()})
    # default=str 兜底，两条都应写入
    assert metric_store.upsert_many([good, with_obj]) == 2


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
