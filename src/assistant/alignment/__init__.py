"""时间对齐：TimeAligner，按时间窗桥接客观指标与主观事件。"""

from .aligner import TimeAligner
from .sqlite_aligner import SQLiteTimeAligner

__all__ = ["TimeAligner", "SQLiteTimeAligner"]
