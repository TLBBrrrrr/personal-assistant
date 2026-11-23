"""Garmin 同步编排器：把佳明各类数据抓成 Metric 落库。

设计要点：
- 抓取器注册表 FETCHERS：每个 kind 一个函数 (client, date) -> list[Metric]。
  新增数据类别只需加一个抓取器，不动编排逻辑。
- 两类抓取：
  * 活动类（activity）：按日期区间一次拉取（get_activities_by_date），单条可带详情。
  * 日指标类（daily/sleep/hrv/...）：逐日拉取，按日期做时间锚点。
- 单类失败隔离：某个 kind 抛错只记入错误列表，不影响其他类别。
- 增量：有游标时只同步游标之后的日期；全量由 start_date 控制。
- payload 保留佳明原始返回，结构化字段（距离/配速/时长等）从原始字段提取到 payload 顶层
  便于查询，但原始 JSON 不丢。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any

from assistant.models import Metric
from assistant.sport.client import GarminClient

log = logging.getLogger(__name__)

# 抓取器签名：(client, 日期) -> list[Metric]
Fetcher = Callable[[GarminClient, str], list[Metric]]


# ---------- 时间工具 ----------
def _day_bounds(day: str) -> tuple[datetime, datetime]:
    """YYYY-MM-DD → 当天 [00:00, 23:59:59.999999] 的 naive datetime。"""
    d = date.fromisoformat(day)
    return datetime.combine(d, time.min), datetime.combine(
        d, time.max
    )


def _anchor(day: str, *, hour: int = 0) -> datetime:
    d = date.fromisoformat(day)
    return datetime.combine(d, time(hour=hour))


def _duration_sec(raw: Any) -> float | None:
    """佳明 duration 字段通常是秒。"""
    if isinstance(raw, (int, float)):
        return float(raw)
    return None


# ---------- 抓取器：活动 ----------
def fetch_activities(client: GarminClient, day: str) -> list[Metric]:
    """拉取某日全部活动（含摘要字段；详情另见 with_details）。"""
    raw_activities = client.raw.get_activities_by_date(day, day)
    metrics: list[Metric] = []
    for a in raw_activities or []:
        aid = str(a.get("activityId") or "")
        if not aid:
            continue
        start = a.get("startTimeLocal") or a.get("startTimeGMT") or day
        try:
            start_dt = datetime.fromisoformat(start)
        except (ValueError, TypeError):
            start_dt = _anchor(day)
        end_dt = None
        if a.get("elapsedDuration"):
            end_dt = start_dt + timedelta(seconds=_duration_sec(a["elapsedDuration"]) or 0)
        payload = dict(a)  # 原始字段全保留
        # 提取常用查询字段到顶层（不存在就不写）
        payload["_distance_km"] = (
            round(a["distance"] / 1000, 3) if a.get("distance") else None
        )
        payload["_duration_min"] = (
            round((a["elapsedDuration"] or 0) / 60, 2)
            if a.get("elapsedDuration")
            else None
        )
        payload["_activity_type"] = (
            (a.get("activityType") or {}).get("typeKey")
            if isinstance(a.get("activityType"), dict)
            else a.get("activityType")
        )
        metrics.append(
            Metric.from_garmin(
                "activity", aid, start_dt, payload, end_time=end_dt
            )
        )
    return metrics


# ---------- 抓取器：日指标（逐日） ----------
def _simple_daily(
    kind: str,
    method: str,
    day: str,
    client: GarminClient,
    *,
    hook: Callable[[dict], dict] | None = None,
) -> list[Metric]:
    """通用单日抓取：调用 client.raw.<method>(day)，整体作为一个 Metric。"""
    fn = getattr(client.raw, method)
    raw = fn(day)
    if not raw:
        return []
    payload = dict(raw) if isinstance(raw, dict) else {"value": raw}
    if hook:
        payload.update(hook(payload))
    return [Metric.from_garmin(kind, day, _anchor(day), payload)]


def fetch_sleep(client: GarminClient, day: str) -> list[Metric]:
    return _simple_daily("sleep", "get_sleep_data", day, client)


def fetch_daily(client: GarminClient, day: str) -> list[Metric]:
    """每日摘要：步数 / 距离 / 卡路里 / 静息心率 等。"""
    return _simple_daily("daily", "get_user_summary", day, client)


def fetch_steps(client: GarminClient, day: str) -> list[Metric]:
    raw = client.raw.get_steps_data(day)
    if not raw:
        return []
    return [
        Metric.from_garmin("steps", day, _anchor(day), {"samples": raw})
    ]


def fetch_hrv(client: GarminClient, day: str) -> list[Metric]:
    return _simple_daily("hrv", "get_hrv_data", day, client)


def fetch_rhr(client: GarminClient, day: str) -> list[Metric]:
    return _simple_daily("rhr", "get_rhr_day", day, client)


def fetch_heart_rate(client: GarminClient, day: str) -> list[Metric]:
    return _simple_daily("heart_rate", "get_heart_rates", day, client)


def fetch_stress(client: GarminClient, day: str) -> list[Metric]:
    return _simple_daily("stress", "get_stress_data", day, client)


def fetch_body_battery(client: GarminClient, day: str) -> list[Metric]:
    """身体电量接口按区间返回，这里取单日。"""
    raw = client.raw.get_body_battery(day, day)
    if not raw:
        return []
    return [Metric.from_garmin("body_battery", day, _anchor(day), {"values": raw})]


def fetch_body_composition(client: GarminClient, day: str) -> list[Metric]:
    return _simple_daily("weight", "get_body_composition", day, client)


def fetch_respiration(client: GarminClient, day: str) -> list[Metric]:
    return _simple_daily("respiration", "get_respiration_data", day, client)


def fetch_spo2(client: GarminClient, day: str) -> list[Metric]:
    return _simple_daily("spo2", "get_spo2_data", day, client)


def fetch_training_readiness(client: GarminClient, day: str) -> list[Metric]:
    raw = client.raw.get_training_readiness(day)
    if not raw:
        return []
    return [Metric.from_garmin("training_readiness", day, _anchor(day), {"values": raw})]


def fetch_training_status(client: GarminClient, day: str) -> list[Metric]:
    return _simple_daily("training_status", "get_training_status", day, client)


def fetch_vo2max(client: GarminClient, day: str) -> list[Metric]:
    return _simple_daily("vo2max", "get_max_metrics", day, client)


def fetch_hydration(client: GarminClient, day: str) -> list[Metric]:
    return _simple_daily("hydration", "get_hydration_data", day, client)


def fetch_intensity(client: GarminClient, day: str) -> list[Metric]:
    return _simple_daily("intensity", "get_intensity_minutes_data", day, client)


def fetch_calories(client: GarminClient, day: str) -> list[Metric]:
    raw = client.raw.get_calories_daily(day, day)
    if not raw:
        return []
    return [Metric.from_garmin("calories", day, _anchor(day), {"values": raw})]


# 抓取器注册表：新增数据类别 = 加一行
FETCHERS: dict[str, Fetcher] = {
    "activity": fetch_activities,
    "sleep": fetch_sleep,
    "daily": fetch_daily,
    "steps": fetch_steps,
    "hrv": fetch_hrv,
    "rhr": fetch_rhr,
    "heart_rate": fetch_heart_rate,
    "stress": fetch_stress,
    "body_battery": fetch_body_battery,
    "weight": fetch_body_composition,
    "respiration": fetch_respiration,
    "spo2": fetch_spo2,
    "training_readiness": fetch_training_readiness,
    "training_status": fetch_training_status,
    "vo2max": fetch_vo2max,
    "hydration": fetch_hydration,
    "intensity": fetch_intensity,
    "calories": fetch_calories,
}


# ---------- 同步结果 ----------
class SyncReport:
    def __init__(self) -> None:
        self.written: dict[str, int] = {}
        self.errors: list[str] = []
        self.days: list[str] = []

    def merge(self, kind: str, n: int) -> None:
        self.written[kind] = self.written.get(kind, 0) + n

    def fail(self, kind: str, day: str, exc: Exception) -> None:
        msg = f"{kind}@{day}: {type(exc).__name__}: {str(exc).splitlines()[0][:120]}"
        self.errors.append(msg)
        log.warning("抓取失败 %s", msg)

    def summary(self) -> str:
        total = sum(self.written.values())
        detail = "、".join(f"{k} {v}" for k, v in sorted(self.written.items()) if v)
        parts = [f"同步完成：共 {total} 条（{len(self.days)} 天）"]
        if detail:
            parts.append(detail)
        if self.errors:
            parts.append(f"失败 {len(self.errors)} 项")
        return "；".join(parts)


def _date_range(start: date, end: date) -> list[str]:
    days: list[str] = []
    cur = start
    while cur <= end:
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


def sync(
    client: GarminClient,
    store: Any,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    days: int | None = None,
    kinds: list[str] | None = None,
    incremental: bool = False,
    sleep_between_days: float = 0.0,
) -> SyncReport:
    """执行同步。

    参数：
    - start_date/end_date：YYYY-MM-DD，闭区间；end 默认今天。
    - days：近 N 天（含今天），优先级低于 start_date。
    - kinds：只同步指定类别，默认全部。
    - incremental：True 时从 store 的游标之后开始，忽略 start_date/days。
    - sleep_between_days：每天之间的间隔秒，缓解风控。
    """
    report = SyncReport()
    today = date.today()

    end = date.fromisoformat(end_date) if end_date else today
    if incremental:
        cursor = store.sync_cursor()
        if cursor is not None:
            start = cursor.date() + timedelta(days=1)
        else:
            start = end - timedelta(days=(days or 1) - 1)
    elif start_date:
        start = date.fromisoformat(start_date)
    elif days:
        start = end - timedelta(days=days - 1)
    else:
        start = end

    if start > end:
        report.summary()
        return report

    day_list = _date_range(start, end)
    report.days = day_list
    active = kinds or list(FETCHERS)

    for i, day in enumerate(day_list):
        for kind in active:
            fetcher = FETCHERS.get(kind)
            if not fetcher:
                report.fail(kind, day, ValueError("未知抓取器"))
                continue
            try:
                metrics = fetcher(client, day)
                report.merge(kind, store.upsert_many(metrics))
            except Exception as exc:  # noqa: BLE001 - 同步边界：单类失败隔离
                report.fail(kind, day, exc)
        # 游标推进到当天，增量下次从次日开始
        try:
            store.set_sync_cursor(datetime.combine(date.fromisoformat(day), time.max))
        except Exception:  # noqa: BLE001
            pass
        if sleep_between_days and i < len(day_list) - 1:
            import time as _t

            _t.sleep(sleep_between_days)

    return report
