"""Garmin 官方 Personal API 测试：客户端、抓取器映射、同步编排。

用 mock client 验证映射与编排，不连真实 API。
只覆盖数据保存与查询核心，不含 Agent 工具 / 时间对齐。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from assistant.metrics.sqlite_store import SQLiteMetricStore
from assistant.models import Metric
from assistant.sport import PersonalAPIClient, sync
from assistant.sport.sync import fetch_activities, fetch_sleep, fetch_steps


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
    assert metric_store.upsert_many([good, with_obj]) == 2


# ---------- API 客户端（mock urllib） ----------
def test_client_loads_key_from_file(tmp_path):
    key_file = tmp_path / ".env_garmin"
    key_file.write_text("GCDC_test.key.abc\n", encoding="utf-8")
    client = PersonalAPIClient.from_file(key_file)
    assert client._api_key == "GCDC_test.key.abc"


def test_client_missing_key_file(tmp_path):
    from assistant.sport.client import GarminAPIError, load_api_key
    with pytest.raises(GarminAPIError):
        load_api_key(tmp_path / "nonexistent")


def test_client_token_cached(monkeypatch, tmp_path):
    """token 换一次后缓存，第二次 get 不重复换 token。"""
    calls = {"token": 0, "paths": []}

    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return b"hdr.payload.sig"

    def fake_urlopen(req, timeout=None):
        if req.method == "POST":
            calls["token"] += 1
        else:
            calls["paths"].append(req.full_url)
            return _json_resp([])
        return FakeResp()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = PersonalAPIClient("GCDC_x.y.z")
    assert client.token() == "hdr.payload.sig"
    assert client.token() == "hdr.payload.sig"
    assert calls["token"] == 1


def _json_resp(obj):
    raw = json.dumps(obj).encode("utf-8")
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return raw
    return R()


# ---------- 抓取器（mock client） ----------
class FakeClient:
    """按 (path, params) 返回预置响应，用于验证抓取器映射。"""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        value = self.routes.get(path)
        if value is None:
            # 路径式区间端点：/xxx/{start}/{end}，按最长前缀匹配
            for key, val in self.routes.items():
                if path.startswith(key.rstrip("/") + "/"):
                    value = val
                    break
        if value is None:
            return None
        if callable(value):
            return value(params)
        return value


def test_fetch_activities_mapping():
    client = FakeClient({
        "/activitylist-service/activities/search/activities": [
            {
                "activityId": 999,
                "startTimeLocal": "2026-01-01 07:30:00",
                "distance": 10000,
                "elapsedDuration": 3600,
                "activityType": {"typeKey": "running"},
                "activityName": "晨跑",
            }
        ]
    })
    metrics = fetch_activities(client, "2026-01-01", "2026-01-31")
    assert len(metrics) == 1
    m = metrics[0]
    assert m.id == "activity:999"
    assert m.payload["_distance_km"] == 10.0
    assert m.payload["_duration_min"] == 60.0
    assert m.payload["_activity_type"] == "running"
    assert m.end_time is not None


def test_fetch_activities_filters_by_date():
    client = FakeClient({
        "/activitylist-service/activities/search/activities": [
            {"activityId": 1, "startTimeLocal": "2025-12-31 08:00:00", "distance": 1000},
            {"activityId": 2, "startTimeLocal": "2026-01-02 08:00:00", "distance": 2000},
        ]
    })
    metrics = fetch_activities(client, "2026-01-01", "2026-01-31")
    assert [m.source_id for m in metrics] == ["2"]


def test_fetch_sleep_mapping():
    client = FakeClient({
        "/sleep-service/sleep/dailySleep": {
            "calendarDate": "2026-01-01",
            "sleepTimeSeconds": 28800,
            "deepSleepSeconds": 7200,
        }
    })
    metrics = fetch_sleep(client, "2026-01-01", "2026-01-01")
    assert len(metrics) == 1
    assert metrics[0].id == "sleep:2026-01-01"
    assert metrics[0].payload["_sleep_hours"] == 8.0


def test_fetch_steps_range_mapping():
    client = FakeClient({
        "/usersummary-service/stats/steps/daily": [
            {"calendarDate": "2026-01-01", "totalSteps": 8000},
            {"calendarDate": "2026-01-02", "totalSteps": 12000},
        ]
    })
    metrics = fetch_steps(client, "2026-01-01", "2026-01-02")
    assert len(metrics) == 2
    assert {m.source_id for m in metrics} == {"2026-01-01", "2026-01-02"}


# ---------- 同步编排 ----------
def test_sync_writes_metrics_and_cursor(metric_store):
    client = FakeClient({
        "/activitylist-service/activities/search/activities": [
            {"activityId": 1, "startTimeLocal": "2026-01-01 08:00:00", "distance": 1000}
        ],
        "/sleep-service/sleep/dailySleep": {"calendarDate": "2026-01-01", "sleepTimeSeconds": 100},
    })
    report = sync(client, metric_store, start_date="2026-01-01",
                  end_date="2026-01-01", kinds=["activity", "sleep"])
    assert metric_store.count("activity") == 1
    assert metric_store.count("sleep") == 1
    assert report.written["activity"] == 1
    assert report.written["sleep"] == 1
    assert metric_store.sync_cursor() is not None
    assert "同步完成" in report.summary()


def test_sync_isolates_fetcher_failure(metric_store):
    def boom(params):
        raise RuntimeError("boom")

    client = FakeClient({
        "/activitylist-service/activities/search/activities": [
            {"activityId": 1, "startTimeLocal": "2026-01-01 08:00:00"}
        ],
        "/sleep-service/sleep/dailySleep": boom,
    })
    report = sync(client, metric_store, start_date="2026-01-01",
                  end_date="2026-01-01", kinds=["activity", "sleep"])
    assert metric_store.count("activity") == 1  # 好的类别照常写入
    assert any("sleep" in e for e in report.errors)
