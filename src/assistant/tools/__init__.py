"""工具注册表：领域能力通过 register 注入，核心不感知领域细节。"""

from .registry import ToolRegistry, build_default_registry

__all__ = ["ToolRegistry", "build_default_registry"]
