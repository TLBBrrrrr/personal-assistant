"""LiteLLM Provider：经 LiteLLM 接任意 OpenAI 兼容模型。

厂商 SDK 的 import 被隔离在本模块内，Agent / core / domains 都不直接依赖。
"""

from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.litellm import LiteLLMProvider


class LiteLLMModelProvider:
    """用 LiteLLMProvider 构造模型。

    model 形如 "dashscope/qwen-plus"、"deepseek/deepseek-chat"、"openai/gpt-4o-mini"。
    api_base 为 OpenAI 兼容端点（结尾带 /v1），留空则走厂商默认。
    """

    def __init__(
        self,
        model: str,
        api_base: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.api_base = api_base
        self.api_key = api_key

    def get_model(self) -> Model:
        provider_kwargs: dict = {}
        if self.api_base:
            provider_kwargs["api_base"] = self.api_base
        if self.api_key:
            provider_kwargs["api_key"] = self.api_key

        provider = LiteLLMProvider(**provider_kwargs)
        return OpenAIChatModel(self.model, provider=provider)

    @property
    def description(self) -> str:
        return f"LiteLLM 模型 {self.model}" + (
            f" @ {self.api_base}" if self.api_base else ""
        )
