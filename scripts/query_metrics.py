"""客观指标查询脚本：直接查本地 metrics.db，不涉及 Agent / 对话工具。

用法（在项目根目录）：
    # 库概况（各类型条数、时间跨度、同步游标）
    .venv/bin/python scripts/query_metrics.py --overview

    # 查某类指标最近 N 条
    .venv/bin/python scripts/query_metrics.py --kind activity --limit 10
    .venv/bin/python scripts/query_metrics.py --kind sleep --limit 5

    # 按时间窗查（YYYY-MM-DD，闭区间）
    .venv/bin/python scripts/query_metrics.py --kind activity --start 2026-01-01 --end 2026-01-31

    # 列出所有可用的 kind
    .venv/bin/python scripts/query_metrics.py --list-kinds

    # 查看某条完整 payload（按 id，如 activity:12345）
    .venv/bin/python scripts/query_metrics.py --id activity:12345
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time
from pathlib import Path

# 允许从项目根目录直接运行脚本时导入 src 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assistant.config import Settings
from assistant.metrics.sqlite_store import SQLiteMetricStore


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="查询本地 Garmin 客观指标库")
    p.add_argument("--overview", action="store_true", help="显示指标库概况")
    p.add_argument("--list-kinds", action="store_true", help="列出所有 kind 及条数")
    p.add_argument("--kind", help="按类型查询，如 activity / sleep / daily")
    p.add_argument("--start", help="起始日期 YYYY-MM-DD")
    p.add_argument("--end", help="结束日期 YYYY-MM-DD")
    p.add_argument("--limit", type=int, default=10, help="最多返回条数，默认 10")
    p.add_argument("--id", help="按完整 id 查看某条 payload，如 activity:12345")
    p.add_argument("--raw", action="store_true", help="输出完整 payload JSON 而非摘要")
    return p


def _summary(metric) -> str:
    """给指标生成一行可读摘要。"""
    p = metric.payload
    when = metric.start_time.strftime("%Y-%m-%d %H:%M")
    if metric.kind == "activity":
        dist = p.get("_distance_km")
        dur = p.get("_duration_min")
        typ = p.get("_activity_type") or "运动"
        name = p.get("activityName") or ""
        parts = [str(typ)]
        if name:
            parts.append(name)
        if dist:
            parts.append(f"{dist} 公里")
        if dur:
            parts.append(f"{dur} 分钟")
        return f"[{when}] {metric.kind}：{' · '.join(parts)}"
    for key in ("totalSteps", "steps", "restingHeartRate", "averageHR",
                "sleepScore", "overallScore", "averageStressLevel", "value"):
        if key in p and p[key] is not None:
            return f"[{when}] {metric.kind}：{key}={p[key]}"
    scalars = [
        f"{k}={v}" for k, v in p.items()
        if isinstance(v, (int, float, str)) and not k.startswith("_")
    ][:4]
    return f"[{when}] {metric.kind}：{'、'.join(scalars) if scalars else '（无摘要字段）'}"


def _show(metrics: list, raw: bool) -> None:
    if not metrics:
        print("没有找到相关指标。")
        return
    for m in metrics:
        if raw:
            print(json.dumps(m.payload, ensure_ascii=False, indent=2, default=str))
            print("-" * 50)
        else:
            print(_summary(m))


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings.from_env()
    store = SQLiteMetricStore(settings.data_dir / settings.metrics_db)

    if store.count() == 0:
        print(f"指标库为空：{store.db_path}")
        print("先用 scripts/sync_garmin.py 同步数据。")
        return

    if args.overview:
        s = store.stats()
        by_kind = "、".join(f"{k} {v}" for k, v in s["by_kind"].items()) or "无"
        print(f"指标库：{store.db_path}")
        print(f"总条数：{s['total']}")
        print(f"分类：{by_kind}")
        if s["oldest"] and s["newest"]:
            print(f"时间跨度：{s['oldest'][:10]} ~ {s['newest'][:10]}")
        print(f"同步游标：{s['cursor']}")
        return

    if args.list_kinds:
        kinds = store.kinds()
        if not kinds:
            print("无数据。")
            return
        for k, n in kinds.items():
            print(f"  {k}: {n}")
        return

    if args.id:
        m = store.get(args.id)
        if m is None:
            print(f"未找到 id：{args.id}")
            return
        print(json.dumps(m.payload, ensure_ascii=False, indent=2, default=str))
        return

    if args.kind:
        if args.start or args.end:
            start = (
                datetime.combine(date.fromisoformat(args.start), time.min)
                if args.start else datetime.min
            )
            end = (
                datetime.combine(date.fromisoformat(args.end), time.max)
                if args.end else datetime.max
            )
            metrics = store.query_range(start, end, kinds=[args.kind], limit=args.limit)
        else:
            metrics = store.query_range(
                datetime.min, datetime.max, kinds=[args.kind], limit=args.limit
            )
        print(f"查询 {args.kind}，最近 {len(metrics)} 条：")
        _show(metrics, args.raw)
        return

    # 没给任何查询条件：显示概况 + 最近 10 条
    print(f"指标库：{store.db_path}，共 {store.count()} 条")
    print(f"分类：{store.kinds()}")
    print("\n最近 10 条：")
    _show(store.query_range(datetime(1, 1, 1), datetime.max, limit=10), args.raw)


if __name__ == "__main__":
    main()
