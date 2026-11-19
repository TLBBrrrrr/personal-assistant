"""Phase 0 验证：对话循环、工具调用、post-turn hook、接口骨架。"""

from __future__ import annotations

import os

import pytest

from assistant.config import Settings
from assistant.core import Conversation, build_agent, log_turn_hook
from assistant.providers import build_provider
from assistant.tools import ToolRegistry, build_default_registry


def test_default_registry_has_tools():
    registry = build_default_registry()
    assert "get_current_time" in registry.names()
    assert "echo" in registry.names()
    assert "已收到: hi" == registry.invoke("echo", text="hi")


def test_registry_custom_tool():
    registry = ToolRegistry()

    @registry.register
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    assert registry.invoke("add", a=2, b=3) == 5


def test_test_provider_offline():
    settings = Settings(provider="test")
    provider = build_provider(settings)
    assert "TestModel" in provider.description


def test_litellm_provider_builds(monkeypatch):
    # 只验证能构造，不实际请求模型
    settings = Settings(provider="litellm", model="openai/gpt-4o-mini")
    provider = build_provider(settings)
    model = provider.get_model()
    assert model is not None
    assert "gpt-4o-mini" in provider.description


def test_multi_turn_conversation_with_hook():
    settings = Settings(provider="test", hook_enabled=True)
    provider = build_provider(settings)
    registry = build_default_registry()
    agent = build_agent(settings, registry, provider)

    conv = Conversation(agent=agent, history_limit=settings.history_limit)
    conv.add_hook(log_turn_hook)

    r1 = conv.run_turn("你好")
    assert r1.output
    assert len(r1.hook_notes) == 1
    assert "hook" in r1.hook_notes[0]

    r2 = conv.run_turn("再说一次")
    assert r2.output
    # 多轮后历史应累积（第 2 轮包含第 1 轮消息）
    assert len(r2.hook_notes) == 1


def test_hook_failure_does_not_break_turn():
    settings = Settings(provider="test")
    provider = build_provider(settings)
    agent = build_agent(settings, build_default_registry(), provider)
    conv = Conversation(agent=agent)

    def bad_hook(user, output, history):
        raise RuntimeError("boom")

    conv.add_hook(bad_hook)
    result = conv.run_turn("测试")
    assert result.output  # 主对话不受影响
    assert any("hook 失败" in n for n in result.hook_notes)


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("ASSISTANT_PROVIDER", "litellm")
    monkeypatch.setenv("ASSISTANT_MODEL", "deepseek/deepseek-chat")
    monkeypatch.setenv("ASSISTANT_HISTORY_LIMIT", "6")
    s = Settings.from_env()
    assert s.provider == "litellm"
    assert s.model == "deepseek/deepseek-chat"
    assert s.history_limit == 6


def test_models_importable():
    from assistant.models import Digest, Event, Metric

    assert Event.model_fields["occurred_at"] is not None
    assert Metric.model_fields["kind"] is not None
    assert Digest.model_fields["period"] is not None


def test_store_protocols_importable():
    from assistant.alignment import TimeAligner
    from assistant.metrics import MetricStore
    from assistant.recall import EventStore

    assert EventStore is not None
    assert MetricStore is not None
    assert TimeAligner is not None
