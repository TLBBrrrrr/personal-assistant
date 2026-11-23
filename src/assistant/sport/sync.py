"""Garmin 同步编排器（官方 Personal API）：抓各类数据映射成 Metric 落库。

设计要点：
- 抓取器注册表 FETCHERS：每个 kind 一个函数 (client, start, end) -> list[Metric]。
  新增数据类别只需加一个抓取器，不动编排逻辑。
- 官方 API 多数是区间端点（传 start/end 一次拿一段），逐条映射为 Metric；
  单日端点（睡眠/心率/健身年龄）则逐日循环。
- 单类失败隔离：某个 kind 抛错只记入错误列表，不影响其他类别。
- 增量：有游标时只同步游标之后的日期；全量由 start_date 控制。
- payload 保留原始返回，常用字段提到 payload 顶层（下划线前缀）便于查询脚本摘要。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any

from assistant.models import Metric
from assistant.sport.client import PersonalAPIClient

log = logging.getLogger(__name__)

# 抓取器签名：(client, start_date, end_date) -> list[Metric]，日期为 YYYY-MM-DD
Fetcher = Callable[[PersonalAPIClient, str, str], list[Metric]]


# ---------- 时间工具 ----------
def _anchor(day: str, *, hour: int = 0) -> datetime:
    """YYYY-MM-DD → 当天锚点（naive datetime，存储层按本地时区补 tz）。"""
    d = date.fromisoformat(day)
    return datetime.combine(d, time(hour=hour))


def _date_range(start: str, end: str) -> list[str]:
    days: list[str] = []
    cur = date.fromisoformat(start)
    last = date.fromisoformat(end)
    while cur <= last:
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


def _parse_start(raw: Any, fallback_day: str) -> datetime:
    """从佳明时间字符串解析 start_time；失败则退回当天锚点。"""
    if isinstance(raw, str) and raw:
        try:
            # "2026-01-01 07:30:00" 或 ISO 8641 都能解析
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return _anchor(fallback_day)


# ---------- 抓取器：活动（区间列表） ----------
def fetch_activities(client: PersonalAPIClient, start: str, end: str) -> list[Metric]:
    """分页拉取活动列表，按 [start, end] 过滤，每条活动一个 Metric。"""
    metrics: list[Metric] = []
    offset = 0
    limit = 100
    # 活动列表按时间倒序，拉到早于 start 或没有更多为止；上限防死循环
    while offset < 2000:
        batch = client.get(
            "/activitylist-service/activities/search/activities",
            {"start": offset, "limit": limit},
        )
        if not isinstance(batch, list) or not batch:
            break
        stop = False
        for a in batch:
            aid = str(a.get("activityId") or "")
            if not aid:
                continue
            start_local = a.get("startTimeLocal") or a.get("startTimeGMT") or ""
            day = start_local[:10]
            if day and day < start:
                stop = True
                continue
            if day and day > end:
                continue
            start_dt = _parse_start(start_local or a.get("startTimeGMT"), day or start)
            end_dt = None
            elapsed = a.get("elapsedDuration")
            if isinstance(elapsed, (int, float)):
                end_dt = start_dt + timedelta(seconds=float(elapsed))
            payload = dict(a)
            payload["_distance_km"] = (
                round(a["distance"] / 1000, 3) if a.get("distance") else None
            )
            payload["_duration_min"] = (
                round(float(elapsed) / 60, 2) if isinstance(elapsed, (int, float)) else None
            )
            atype = a.get("activityType")
            payload["_activity_type"] = (
                atype.get("typeKey") if isinstance(atype, dict) else atype
            )
            metrics.append(
                Metric.from_garmin("activity", aid, start_dt, payload, end_time=end_dt)
            )
        if stop or len(batch) < limit:
            break
        offset += limit
    return metrics


# ---------- 抓取器：区间端点（一次拉一段，逐条映射） ----------
def _range_items(
    client: PersonalAPIClient,
    path: str,
    start: str,
    end: str,
    *,
    params_style: str = "path",
    wrapper: str | None = None,
    date_key: str = "calendarDate",
) -> list[dict]:
    """调用区间端点并取出条目列表。

    - params_style="path"：日期走路径 /{start}/{end}
    - params_style="query"：日期走 query startDate/endDate
    - wrapper：响应是 {"<wrapper>": [...]} 时取该字段
    """
    if params_style == "path":
        raw = client.get(f"{path}/{start}/{end}")
    else:
        raw = client.get(path, {"startDate": start, "endDate": end})
    if wrapper:
        raw = raw.get(wrapper) if isinstance(raw, dict) else None
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        # 单日 dict：包成一条
        return [raw]
    return []


def _map_daily(
    client: PersonalAPIClient,
    kind: str,
    start: str,
    end: str,
    *,
    path: str,
    params_style: str = "path",
    wrapper: str | None = None,
    date_key: str = "calendarDate",
    hook: Callable[[dict], dict] | None = None,
) -> list[Metric]:
    items = _range_items(
        client, path, start, end,
        params_style=params_style, wrapper=wrapper, date_key=date_key,
    )
    metrics: list[Metric] = []
    for item in items:
        day = str(item.get(date_key) or start)[:10]
        if not (start <= day <= end):
            continue
        payload = dict(item)
        if hook:
            payload.update(hook(payload))
        metrics.append(Metric.from_garmin(kind, day, _anchor(day), payload))
    return metrics


def fetch_steps(client: PersonalAPIClient, start: str, end: str) -> list[Metric]:
    return _map_daily(
        client, "steps", start, end,
        path="/usersummary-service/stats/steps/daily",
    )


def fetch_stress(client: PersonalAPIClient, start: str, end: str) -> list[Metric]:
    return _map_daily(
        client, "stress", start, end,
        path="/usersummary-service/stats/stress/daily",
        hook=lambda p: {"_avg_stress": p.get("values")},
    )


def fetch_body_battery(client: PersonalAPIClient, start: str, end: str) -> list[Metric]:
    return _map_daily(
        client, "body_battery", start, end,
        path="/wellness-service/wellness/bodyBattery/reports/daily",
        params_style="query", date_key="date",
    )


def fetch_hrv(client: PersonalAPIClient, start: str, end: str) -> list[Metric]:
    return _map_daily(
        client, "hrv", start, end,
        path="/hrv-service/hrv/daily", wrapper="hrvSummaries",
    )


def fetch_weight(client: PersonalAPIClient, start: str, end: str) -> list[Metric]:
    return _map_daily(
        client, "weight", start, end,
        path="/weight-service/weight/range", wrapper="dailyWeightSummaries",
        date_key="calendarDate",
    )


def fetch_daily(client: PersonalAPIClient, start: str, end: str) -> list[Metric]:
    """每日摘要 v2：含步数/卡路里/心率/压力/电量/呼吸等综合数据。"""
    return _map_daily(
        client, "daily", start, end,
        path="/usersummary-service/v2/dailysummaries",
        params_style="query", wrapper="data",
    )


# ---------- 抓取器：单日端点（逐日循环） ----------
def fetch_sleep(client: PersonalAPIClient, start: str, end: str) -> list[Metric]:
    metrics: list[Metric] = []
    for day in _date_range(start, end):
        raw = client.get("/sleep-service/sleep/dailySleep", {"date": day})
        if not isinstance(raw, dict) or not raw:
            continue
        payload = dict(raw)
        payload["_sleep_hours"] = (
            round(raw["sleepTimeSeconds"] / 3600, 2)
            if isinstance(raw.get("sleepTimeSeconds"), (int, float)) else None
        )
        metrics.append(Metric.from_garmin("sleep", day, _anchor(day), payload))
    return metrics


def fetch_heart_rate(client: PersonalAPIClient, start: str, end: str) -> list[Metric]:
    metrics: list[Metric] = []
    for day in _date_range(start, end):
        raw = client.get("/wellness-service/wellness/dailyHeartRate", {"date": day})
        if not isinstance(raw, dict) or not raw:
            continue
        payload = dict(raw)
        payload["_resting_hr"] = raw.get("restingHeartRate")
        metrics.append(Metric.from_garmin("heart_rate", day, _anchor(day), payload))
    return metrics


def fetch_rhr(client: PersonalAPIClient, start: str, end: str) -> list[Metric]:
    """静息心率：从每日心率里抽 restingHeartRate 单独存一类，便于趋势查询。"""
    metrics: list[Metric] = []
    for day in _date_range(start, end):
        raw = client.get("/wellness-service/wellness/dailyHeartRate", {"date": day})
        if not isinstance(raw, dict):
            continue
        rhr = raw.get("restingHeartRate")
        if rhr is None:
            continue
        metrics.append(
            Metric.from_garmin("rhr", day, _anchor(day), {"restingHeartRate": rhr})
        )
    return metrics


def fetch_fitness_age(client: PersonalAPIClient, start: str, end: str) -> list[Metric]:
    metrics: list[Metric] = []
    for day in _date_range(start, end):
        raw = client.get(f"/fitnessage-service/fitnessage/v2/{day}")
        if not isinstance(raw, dict) or not raw:
            continue
        metrics.append(Metric.from_garmin("fitness_age", day, _anchor(day), dict(raw)))
    return metrics


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
    "weight": fetch_weight,
    "fitness_age": fetch_fitness_age,
}


# ---------- 同步结果 ----------
class SyncReport:
    def __init__(self) -> None:
        self.written: dict[str, int] = {}
        self.errors: list[str] = []

    def merge(self, kind: str, n: int) -> None:
        self.written[kind] = self.written.get(kind, 0) + n

    def fail(self, kind: str, scope: str, exc: Exception) -> None:
        msg = f"{kind}@{scope}: {type(exc).__name__}: {str(exc).splitlines()[0][:120]}"
        self.errors.append(msg)
        log.warning("抓取失败 %s", msg)

    def summary(self) -> str:
        total = sum(self.written.values())
        detail = "、".join(f"{k} {v}" for k, v in sorted(self.written.items()) if v)
        parts = [f"同步完成：共 {total} 条"]
        if detail:
            parts.append(detail)
        if self.errors:
            parts.append(f"失败 {len(self.errors)} 项")
        return "；".join(parts)


def sync(
    client: PersonalAPIClient,
    store: Any,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    days: int | None = None,
    kinds: list[str] | None = None,
    incremental: bool = False,
) -> SyncReport:
    """执行同步。

    参数：
    - start_date/end_date：YYYY-MM-DD，闭区间；end 默认今天。
    - days：近 N 天（含今天），优先级低于 start_date。
    - kinds：只同步指定类别，默认全部。
    - incremental：True 时从 store 的游标之后开始，忽略 start_date/days。
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
        return report

    start_s, end_s = start.isoformat(), end.isoformat()
    active = kinds or list(FETCHERS)
    scope = f"{start_s}~{end_s}"

    for kind in active:
        fetcher = FETCHERS.get(kind)
        if not fetcher:
            report.fail(kind, scope, ValueError("未知抓取器"))
            continue
        try:
            metrics = fetcher(client, start_s, end_s)
            report.merge(kind, store.upsert_many(metrics))
        except Exception as exc:  # noqa: BLE001 - 同步边界：单类失败隔离
            report.fail(kind, scope, exc)

    # 全部完成后推进游标到当天末尾，增量下次从次日开始
    try:
        store.set_sync_cursor(datetime.combine(end, time.max))
    except Exception:  # noqa: BLE001
        pass

    return report
