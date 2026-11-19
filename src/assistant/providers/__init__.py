"""模型 Provider：把 Agent 与具体模型厂商解耦。

Agent 只依赖 ModelProvider 协议返回的 model 对象，不 import 任何厂商 SDK。
"""

from .base import ModelProvider, build_provider
from .litellm_provider import LiteLLMModelProvider
from .test_provider import TestModelProvider

__all__ = [
    "ModelProvider",
    "build_provider",
    "LiteLLMModelProvider",
    "TestModelProvider",
]
