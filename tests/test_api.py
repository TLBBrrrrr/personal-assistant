"""HTTP API 测试：鉴权、会话隔离、工具调用、错误处理。"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from assistant.api import create_app
from assistant.config import Settings


@pytest.fixture
def api_key(monkeypatch):
    key = "test-secret-key"
    monkeypatch.setenv("ASSISTANT_API_KEY_TOKEN", key)
    monkeypatch.setenv("ASSISTANT_PROVIDER", "test")
    return key


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_DATA_DIR", str(tmp_path))
    settings = Settings.from_env()
    return TestClient(create_app(settings))


def test_health_public(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "sessions" in r.json()


def test_chat_requires_key(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/chat", json={"message": "你好"})
    assert r.status_code in (401, 503)


def test_chat_wrong_key(tmp_path, monkeypatch, api_key):
    client = _client(tmp_path, monkeypatch)
    r = client.post(
        "/chat", json={"message": "你好"}, headers={"X-API-Key": "wrong"}
    )
    assert r.status_code == 401


def test_chat_ok(tmp_path, monkeypatch, api_key):
    client = _client(tmp_path, monkeypatch)
    r = client.post(
        "/chat",
        json={"message": "你好", "session_id": "s1"},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"] == "s1"
    assert isinstance(data["reply"], str) and data["reply"]
    assert data["turn_count"] >= 1


def test_sessions_isolated(tmp_path, monkeypatch, api_key):
    client = _client(tmp_path, monkeypatch)
    h = {"X-API-Key": api_key}
    client.post("/chat", json={"message": "第一轮", "session_id": "a"}, headers=h)
    client.post("/chat", json={"message": "第二轮", "session_id": "a"}, headers=h)
    r2a = client.post(
        "/chat", json={"message": "第三轮", "session_id": "a"}, headers=h
    ).json()
    r2b = client.post(
        "/chat", json={"message": "另一终端", "session_id": "b"}, headers=h
    ).json()
    assert r2a["turn_count"] == 3
    assert r2b["turn_count"] == 1


def test_reset_session(tmp_path, monkeypatch, api_key):
    client = _client(tmp_path, monkeypatch)
    h = {"X-API-Key": api_key}
    client.post("/chat", json={"message": "旧会话", "session_id": "x"}, headers=h)
    r = client.post(
        "/chat",
        json={"message": "重新开始", "session_id": "x", "reset": True},
        headers=h,
    ).json()
    assert r["turn_count"] == 1
