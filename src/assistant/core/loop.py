"""对话循环：管理多轮消息历史，并在每轮结束后触发 post-turn hook。

Phase 0 核心交付。hook 机制是 Phase 1B「对话随口记」的挂点：
- 现在：hook 只记录日志
- Phase 1B：hook 换成事件抽取 + 落库，本模块不用改
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic_ai import Agent


@dataclass
class TurnResult:
    """一轮对话的结果。"""

    user_input: str
    output: str
    hook_notes: list[str] = field(default_factory=list)


# post-turn hook 签名：(user_input, assistant_output, message_history) -> 备注字符串或 None
PostTurnHook = Callable[[str, str, list[Any]], str | None]


@dataclass
class Conversation:
    """一次会话，持有跨轮消息历史与 hook 列表。"""

    agent: "Agent"
    history_limit: int = 0
    hooks: list[PostTurnHook] = field(default_factory=list)
    _messages: list[Any] = field(default_factory=list)

    def add_hook(self, hook: PostTurnHook) -> None:
        self.hooks.append(hook)

    def _trim(self, messages: list[Any]) -> list[Any]:
        """按配置截断历史（0 表示不截断）。"""
        if self.history_limit <= 0:
            return messages
        return messages[-self.history_limit :]

    def run_turn(self, user_input: str) -> TurnResult:
        """执行一轮对话：调用 Agent → 更新历史 → 触发 hooks。"""
        # 多轮对话：用 message_history 传入历史消息列表
        kwargs: dict[str, Any] = {}
        if self._messages:
            kwargs["message_history"] = self._messages

        result = self.agent.run_sync(user_input, **kwargs)
        self._messages = self._trim(result.all_messages())

        # post-turn hooks：每轮结束后依次执行，失败不影响主对话
        notes: list[str] = []
        for hook in self.hooks:
            try:
                note = hook(user_input, result.output, self._messages)
                if note:
                    notes.append(note)
            except Exception as exc:  # hook 失败只记录，不打断对话
                notes.append(f"[hook 失败: {type(exc).__name__}: {exc}]")

        return TurnResult(
            user_input=user_input,
            output=result.output,
            hook_notes=notes,
        )

    @property
    def turn_count(self) -> int:
        """已进行的轮数（按 user 消息数估算）。"""
        return sum(
            1
            for m in self._messages
            if getattr(m, "parts", None)
            and any(
                type(p).__name__ == "UserPromptPart" for p in m.parts
            )
        )


def run_turn(agent: "Agent", user_input: str, conversation: list[Any] | None = None) -> TurnResult:
    """便捷函数：无状态地跑一轮（用于测试）。"""
    conv = Conversation(agent=agent)
    if conversation:
        conv._messages = conversation
    return conv.run_turn(user_input)
