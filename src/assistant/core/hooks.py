"""Post-turn hooks。

Phase 0：只有一个日志 hook，证明挂点链路可用。
Phase 1B：在这里加入「事件抽取 + 落库」hook，loop.py 无需改动。
"""

from __future__ import annotations

from typing import Any


def log_turn_hook(
    user_input: str,
    assistant_output: str,
    message_history: list[Any],
) -> str:
    """在控制台打印一行回合日志，作为 hook 生效的可视化证据。"""
    preview = user_input.replace("\n", " ")[:40]
    return f"[hook] 记录一轮对话（用户: {preview!r}，历史 {len(message_history)} 条）"
