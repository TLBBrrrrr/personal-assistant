"""ModelProvider 协议与工厂。

设计约束（来自总体设计 4.1）：
- Agent 逻辑只依赖本协议的 get_model()，不 import 任何厂商 SDK。
- 厂商 SDK 的 import 只允许出现在 providers/ 子模块内。
"""

from __future__ import annotations

from typing import Protocol

from pydantic_ai.models import Model

from assistant.config import Settings


class ModelProvider(Protocol):
    """返回一个 PydanticAI Model 实例。"""

    def get_model(self) -> Model:
        """构造并返回模型对象。"""
        ...

    @property
    def description(self) -> str:
        """人类可读的提供者描述，用于 CLI 启动提示。"""
        ...


def build_provider(settings: Settings) -> ModelProvider:
    """根据配置选择 Provider 实现。

    - test    → 离线 TestModel（无需 key，用于跑通循环与测试）
    - litellm → 经 LiteLLMProvider 接任意 OpenAI 兼容模型
    """
    # 延迟 import，避免 test 模式也依赖 litellm
    if settings.provider == "test":
        from .test_provider import TestModelProvider

        return TestModelProvider()

    if settings.provider == "litellm":
        from .litellm_provider import LiteLLMModelProvider

        return LiteLLMModelProvider(
            model=settings.model,
            api_base=settings.api_base,
            api_key=settings.api_key,
        )

    raise ValueError(
        f"未知的 provider: {settings.provider!r}，可选值为 'test' 或 'litellm'"
    )
