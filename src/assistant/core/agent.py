"""Agent 构建：把 Provider 提供的模型 + 工具注册表组装成 PydanticAI Agent。"""

from __future__ import annotations

from pydantic_ai import Agent

from assistant.config import Settings
from assistant.prompts import load_system_prompt
from assistant.providers import ModelProvider, build_provider
from assistant.tools import ToolRegistry


def build_agent(
    settings: Settings,
    registry: ToolRegistry,
    provider: ModelProvider | None = None,
) -> Agent:
    """构建 Agent。

    - 模型来自 Provider（协议），不直接依赖具体厂商。
    - 工具来自 ToolRegistry（普通函数列表）。
    """
    provider = provider or build_provider(settings)
    model = provider.get_model()
    tool_fns = [spec.fn for spec in registry.list_tools()]

    agent = Agent(
        model,
        instructions=load_system_prompt(),
        tools=tool_fns,
    )
    return agent
