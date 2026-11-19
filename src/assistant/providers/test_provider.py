"""离线测试 Provider：用 PydanticAI 内置 TestModel 跑通循环，无需 API key。

用于 Phase 0 验证 CLI、工具调用、post-turn hook 全链路。
"""

from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel


class TestModelProvider:
    """返回 TestModel——它会按注册的工具生成调用并产出可预测文本。"""

    def get_model(self) -> Model:
        return TestModel()

    @property
    def description(self) -> str:
        return "离线测试模型（TestModel，无需 API key）"
