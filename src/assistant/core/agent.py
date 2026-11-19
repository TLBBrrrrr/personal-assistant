"""Agent 构建：把 Provider 提供的模型 + 工具注册表组装成 PydanticAI Agent。"""

from __future__ import annotations

from pydantic_ai import Agent

from assistant.config import Settings
from assistant.providers import ModelProvider, build_provider
from assistant.tools import ToolRegistry

SYSTEM_PROMPT = """你是一个个人助理。你可以调用工具来获取信息或执行操作。
回答简洁、准确。用中文回复。"""


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
        instructions=SYSTEM_PROMPT,
        tools=tool_fns,
    )
    return agent
