"""运动领域：Garmin Connect 客户端、同步编排与查询工具。"""

from .client import GarminAuthError, GarminClient, connect
from .sync import FETCHERS, SyncReport, sync

__all__ = [
    "GarminAuthError",
    "GarminClient",
    "connect",
    "FETCHERS",
    "SyncReport",
    "sync",
]
