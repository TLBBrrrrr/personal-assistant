"""提示词资源：从包内 Markdown 加载，改文案无需改代码。

- system.md：主对话 Agent 的系统提示词。
- 用 importlib.resources 读取，兼容源码运行与 wheel 安装。
"""

from __future__ import annotations

from importlib import resources


def load_system_prompt() -> str:
    """读取主对话系统提示词（system.md），去除首尾空白。"""
    text = (
        resources.files("assistant.prompts")
        .joinpath("system.md")
        .read_text(encoding="utf-8")
    )
    return text.strip()


__all__ = ["load_system_prompt"]
