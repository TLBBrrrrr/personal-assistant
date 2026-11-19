"""CLI 入口：Phase 0 的交互界面。

用法：
    python -m assistant.cli              # 默认 test 模式，离线可跑
    ASSISTANT_PROVIDER=litellm ...       # 切到真实模型
    对话中输入 exit / quit / 退出 结束。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from assistant.config import Settings
from assistant.core import Conversation, build_agent, log_turn_hook
from assistant.providers import build_provider
from assistant.tools import build_default_registry

EXIT_COMMANDS = {"exit", "quit", "q", "退出", "bye"}


def _print_model_error(exc: Exception, provider) -> None:
    """把模型调用异常翻译成简洁提示，避免满屏 traceback。"""
    name = type(exc).__name__
    text = str(exc)
    if "401" in text or "Authentication" in name or "Incorrect API key" in text:
        hint = "鉴权失败：API Key 无效、已过期，或与该端点不匹配。"
    elif "404" in text or "not found" in text.lower():
        hint = "端点或模型名不存在，请检查 API Base 与模型名。"
    elif "429" in text:
        hint = "请求过于频繁或额度不足，请稍后重试 / 检查配额。"
    elif any(k in name for k in ("Connection", "Timeout", "Network", "APIStatus")):
        hint = "网络或服务端错误，请检查网络与端点可达性。"
    else:
        hint = "模型调用出错。"
    print(f"\n[模型错误] {name}: {hint}")
    print("  运行自检定位问题： .venv/bin/python scripts/probe_api.py")
    print(f"  当前配置：{provider.description}")
    print("  详情：" + text.splitlines()[0][:200])


def main() -> None:
    settings = Settings.from_env()
    provider = build_provider(settings)
    registry = build_default_registry()
    agent = build_agent(settings, registry, provider)

    conversation = Conversation(agent=agent, history_limit=settings.history_limit)
    if settings.hook_enabled:
        conversation.add_hook(log_turn_hook)

    print("=" * 56)
    print("个人助理 · Phase 0 对话循环")
    print(f"模型: {provider.description}")
    print(f"工具: {', '.join(registry.names())}")
    print(f"Hook: {'启用' if settings.hook_enabled else '关闭'}")
    print("输入 exit / quit / 退出 结束对话")
    print("=" * 56)

    while True:
        try:
            user_input = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已结束。")
            break

        if not user_input:
            continue
        if user_input.lower() in EXIT_COMMANDS:
            print("已结束。")
            break

        try:
            result = conversation.run_turn(user_input)
        except Exception as exc:  # noqa: BLE001 - CLI 边界：把模型错误翻译成可读提示
            _print_model_error(exc, provider)
            break
        print(f"\n助理> {result.output}")
        for note in result.hook_notes:
            print(f"       {note}")


if __name__ == "__main__":
    main()
