#!/usr/bin/env python3
"""查看个人助理的事件数据库（SQLite）。

用法：
    python scripts/view_db.py                          # 默认看 data/events.db
    python scripts/view_db.py /tmp/pa_e2e_data/events.db  # 指定库文件
    python scripts/view_db.py -k 跑步                   # 按关键词过滤
    python scripts/view_db.py -v                        # 显示完整正文
    python scripts/view_db.py --sql "SELECT * FROM events LIMIT 5"  # 跑任意 SQL

只依赖标准库，不需要激活项目的虚拟环境也能跑。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import unicodedata
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "events.db"


def display_width(s: str) -> int:
    """按中英文字符实际宽度计算，用于表格对齐。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s: str, width: int) -> str:
    s = str(s)
    return s + " " * max(0, width - display_width(s))


def print_table(headers: list[str], rows: list[list[object]]) -> None:
    widths = [display_width(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], display_width(str(cell)))
    line = "  ".join(pad(h, widths[i]) for i, h in enumerate(headers))
    print(line)
    print("-" * display_width(line))
    for row in rows:
        print("  ".join(pad(str(c), widths[i]) for i, c in enumerate(row)))


def main() -> int:
    parser = argparse.ArgumentParser(description="查看事件数据库")
    parser.add_argument("db", nargs="?", default=str(DEFAULT_DB), help="数据库文件路径")
    parser.add_argument("-k", "--keyword", help="按标题/正文/标签过滤（模糊匹配）")
    parser.add_argument("-v", "--verbose", action="store_true", help="显示完整正文")
    parser.add_argument("--sql", help="执行任意 SQL 并打印结果")
    args = parser.parse_args()

    db = Path(args.db).expanduser()
    if not db.exists():
        print(f"数据库不存在：{db}")
        print("提示：先用 CLI 随口记一件事，或指定测试库：")
        print("  python scripts/view_db.py /tmp/pa_e2e_data/events.db")
        return 1

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if args.sql:
        cur.execute(args.sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchall()]
        print(f"SQL: {args.sql}\n")
        if cols:
            print_table(cols, rows)
            print(f"\n共 {len(rows)} 行")
        else:
            print("（无返回结果）")
        conn.close()
        return 0

    try:
        total = cur.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    except sqlite3.OperationalError as exc:
        print(f"不是有效的事件库：{exc}")
        conn.close()
        return 1

    print(f"数据库：{db}")
    print(f"事件总数：{total}")
    types = cur.execute("SELECT type, COUNT(*) FROM events GROUP BY type ORDER BY COUNT(*) DESC").fetchall()
    if types:
        print("类型分布：" + "、".join(f"{r[0]} {r[1]}" for r in types))
    span = cur.execute("SELECT MIN(occurred_at), MAX(occurred_at) FROM events").fetchone()
    if span and span[0]:
        print(f"时间跨度：{span[0][:10]} ~ {span[1][:10]}")
    print()

    where, params = "", []
    if args.keyword:
        where = "WHERE title LIKE ? OR content LIKE ? OR tags LIKE ?"
        kw = f"%{args.keyword}%"
        params = [kw, kw, kw]
    cur.execute(
        f"SELECT * FROM events {where} ORDER BY occurred_at DESC",
        params,
    )
    rows = cur.fetchall()
    if not rows:
        print("（没有匹配的事件）")
        conn.close()
        return 0

    headers = ["发生时间", "类型", "标题", "标签", "心情", "来源"]
    table = [
        [
            r["occurred_at"][:16].replace("T", " "),
            r["type"],
            r["title"],
            ", ".join(__import__("json").loads(r["tags"] or "[]")),
            r["mood"] or "-",
            r["source"],
        ]
        for r in rows
    ]
    print_table(headers, table)
    print(f"\n显示 {len(rows)} 条")

    if args.verbose:
        print("\n=== 完整正文 ===")
        for r in rows:
            print(f"\n[{r['occurred_at'][:16].replace('T', ' ')}] {r['title']}")
            print(f"  正文：{r['content']}")
            print(f"  实体：{r['entities']}  重要度：{r['importance']}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
