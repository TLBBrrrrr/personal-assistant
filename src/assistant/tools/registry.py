"""ToolRegistry：收集工具函数并交给 PydanticAI Agent。

设计要点（来自总体设计 4.4 / 4.6）：
- 领域工具写成普通 Python 函数（带 docstring 与类型注解），PydanticAI 自动转成工具 schema。
- 注册表集中管理工具，核心不 import 领域实现。
- Phase 3 之后 MCP 工具也统一转成普通函数注册进来。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    name: str
    fn: Callable[..., Any]
    description: str


class ToolRegistry:
    """轻量工具注册表。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        fn: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Callable[..., Any]:
        """注册工具，可作为装饰器或普通调用使用。

        用法：
            @registry.register
            def my_tool(x: int) -> str: ...

            registry.register(my_tool, name="别名", description="...")
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            # 去掉内部函数的前导下划线，避免暴露 _get_time 这类名字给模型
            tool_name = name or func.__name__.lstrip("_")
            doc = description or inspect.getdoc(func) or ""
            if tool_name in self._tools:
                raise ValueError(f"工具已存在: {tool_name}")
            self._tools[tool_name] = ToolSpec(
                name=tool_name, fn=func, description=doc
            )
            return func

        if fn is not None:
            return decorator(fn)
        return decorator

    def list_tools(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools)

    def invoke(self, name: str, **kwargs: Any) -> Any:
        if name not in self._tools:
            raise KeyError(f"未注册的工具: {name}")
        return self._tools[name].fn(**kwargs)


def get_current_time() -> str:
    """获取当前本地时间，返回 ISO 8601 格式字符串。"""
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


def echo(text: str) -> str:
    """回显输入的文本，用于验证工具调用链路。"""
    return f"已收到: {text}"


def build_default_registry() -> ToolRegistry:
    """构造含基础工具的注册表（Phase 0 用于验证工具调用）。"""
    registry = ToolRegistry()
    registry.register(get_current_time)
    registry.register(echo)
    return registry
