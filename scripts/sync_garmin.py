"""Garmin 数据同步脚本（官方 Connect Personal API，中国区）。

用法（在项目根目录）：
    # 首次同步最近 30 天
    .venv/bin/python scripts/sync_garmin.py --days 30

    # 指定日期区间
    .venv/bin/python scripts/sync_garmin.py --start 2026-11-01 --end 2026-11-23

    # 只同步活动
    .venv/bin/python scripts/sync_garmin.py --days 7 --kinds activity

    # 增量（从上次游标之后）
    .venv/bin/python scripts/sync_garmin.py --incremental

    # 先试跑、不连接、不落库
    .venv/bin/python scripts/sync_garmin.py --days 3 --dry-run

凭据：裸 Personal API Key 存 .env_garmin（一行，GCDC_ 开头），已 gitignore。
从 Garmin Connect 网页设置页创建：connect.garmin.com/app/settings/personalApiToken
本 key 为中国区，固定连 personal.connectapi.garmin.cn。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许从项目根目录直接运行脚本时导入 src 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assistant.config import Settings
from assistant.metrics.sqlite_store import SQLiteMetricStore
from assistant.sport import FETCHERS, GarminAPIError, PersonalAPIClient, sync


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="同步 Garmin 运动数据到本地指标库（官方 Personal API）")
    p.add_argument("--start", help="起始日期 YYYY-MM-DD")
    p.add_argument("--end", help="结束日期 YYYY-MM-DD，默认今天")
    p.add_argument("--days", type=int, help="同步最近 N 天（含今天）")
    p.add_argument("--kinds", help="逗号分隔的类别，默认全部；可选：" + ",".join(FETCHERS))
    p.add_argument("--incremental", action="store_true", help="从上次同步游标之后增量同步")
    p.add_argument("--dry-run", action="store_true", help="仅打印配置，不连接、不落库")
    p.add_argument("--key-file", help="覆盖 API Key 文件路径（默认取配置 .env_garmin）")
    return p


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings.from_env()
    store = SQLiteMetricStore(settings.data_dir / settings.metrics_db)

    kinds = [k.strip() for k in args.kinds.split(",")] if args.kinds else None
    if kinds:
        unknown = [k for k in kinds if k not in FETCHERS]
        if unknown:
            print(f"[错误] 未知类别：{', '.join(unknown)}")
            print(f"可选类别：{', '.join(FETCHERS)}")
            return

    key_file = args.key_file or settings.garmin_api_key_file
    if args.dry_run:
        print("试运行（不连接 Garmin）：")
        print(f"  指标库：{store.db_path}")
        print(f"  API Key 文件：{key_file}")
        print(f"  类别：{', '.join(kinds) if kinds else '全部（' + str(len(FETCHERS)) + ' 类）'}")
        print(f"  范围：start={args.start} end={args.end} days={args.days} "
              f"incremental={args.incremental}")
        print(f"  当前游标：{store.sync_cursor()}")
        print(f"  现有条数：{store.count()}")
        return

    print("正在连接 Garmin Personal API…")
    try:
        client = PersonalAPIClient.from_file(key_file)
        # 先换一次 token，尽早暴露凭据问题
        client.token()
    except GarminAPIError as exc:
        print(f"[连接失败] {exc}")
        return

    print("认证成功，开始同步…")
    with client:
        report = sync(
            client,
            store,
            start_date=args.start,
            end_date=args.end,
            days=args.days,
            kinds=kinds,
            incremental=args.incremental,
        )

    print(report.summary())
    print(f"指标库现有 {store.count()} 条，分类：{store.kinds()}")
    if report.errors:
        print("\n失败明细（前 10 条）：")
        for err in report.errors[:10]:
            print(f"  - {err}")


if __name__ == "__main__":
    main()
