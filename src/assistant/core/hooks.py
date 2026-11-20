"""Post-turn hooks。

Phase 0：日志 hook，证明挂点链路可用。
Phase 1B：记录 hook，把对话里的生活事件抽取并落库；loop.py 无需改动。
"""

from __future__ import annotations

from typing import Any

from assistant.recall import EventExtractor


def log_turn_hook(
    user_input: str,
    assistant_output: str,
    message_history: list[Any],
) -> str:
    """在控制台打印一行回合日志，作为 hook 生效的可视化证据。"""
    preview = user_input.replace("\n", " ")[:40]
    return f"[hook] 记录一轮对话（用户: {preview!r}，历史 {len(message_history)} 条）"


def make_record_hook(extractor: EventExtractor):
    """工厂：返回一个 post-turn hook，抽取并落库生活事件。

    返回值的备注会显示在 CLI，便于观察“随口记”是否生效。
    抽取器内部吞掉异常，这里只做一层兜底，保证 hook 永不打断对话。
    """

    def record_hook(
        user_input: str,
        assistant_output: str,
        message_history: list[Any],
    ) -> str | None:
        try:
            events = extractor.extract(user_input, assistant_output)
        except Exception as exc:  # 双保险
            return f"[记录失败: {type(exc).__name__}]"
        if not events:
            return None
        titles = "、".join(e.title for e in events[:3])
        more = " 等" if len(events) > 3 else ""
        return f"[已记录 {len(events)} 条事件：{titles}{more}]"

    return record_hook
