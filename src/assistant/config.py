"""配置管理：从环境变量 / .env 读取，集中在这里，不散落各处。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """应用配置。

    模型层可插拔：通过 provider 字段选择实现。
    - "test": PydanticAI 内置 TestModel，离线跑通循环（无需 API key）
    - "litellm": 经 LiteLLMProvider 接任意 OpenAI 兼容模型
    """

    provider: str = "test"
    model: str = "openai/gpt-4o-mini"
    api_base: str | None = None
    api_key: str | None = None

    hook_enabled: bool = True
    history_limit: int = 0          # 0 = 不截断
    data_dir: Path = field(default_factory=lambda: Path("data"))

    # Phase 1B：生活事件自动记录与回忆
    auto_record: bool = True        # 每轮对话后是否自动抽取并记录事件
    events_db: str = "events.db"    # EventStore 数据库文件名

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> "Settings":
        load_dotenv(env_file) if env_file else load_dotenv()

        def _int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)))
            except ValueError:
                return default

        def _bool(name: str, default: bool) -> bool:
            val = os.getenv(name)
            if val is None:
                return default
            return val.strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            provider=os.getenv("ASSISTANT_PROVIDER", "test").strip().lower(),
            model=os.getenv("ASSISTANT_MODEL", "openai/gpt-4o-mini").strip(),
            api_base=os.getenv("ASSISTANT_API_BASE") or None,
            api_key=os.getenv("ASSISTANT_API_KEY") or None,
            hook_enabled=_bool("ASSISTANT_HOOK_ENABLED", True),
            history_limit=max(0, _int("ASSISTANT_HISTORY_LIMIT", 0)),
            data_dir=Path(os.getenv("ASSISTANT_DATA_DIR", "data")),
            auto_record=_bool("ASSISTANT_AUTO_RECORD", True),
            events_db=os.getenv("ASSISTANT_EVENTS_DB", "events.db").strip(),
        )
