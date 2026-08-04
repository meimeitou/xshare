#!/usr/bin/env python3
"""XShare CLI - 数据库 / 持仓 / MCP Server / Web API 管理

用法:
  xshare db init                              # 初始化 DuckDB(OLAP) + SQLite(OLTP) 表结构
  xshare portfolio import <csv> [--dry-run]   # 从 CSV 导入持仓
  xshare serve [--tushare-token TOKEN]        # 运行 MCP Server
  xshare web [--host HOST] [--port PORT]      # 启动 Web API
  xshare --help

同步任务请在 Web 前端 /sync 页面操作（或 MCP sync_job 工具）。
"""

import argparse
import asyncio
import csv
import os
import sys
from datetime import date

def _cmd_db_init(args) -> int:
    """初始化本地数据库：DuckDB（OLAP）+ SQLite（OLTP）"""
    from xshare.data.db import DEFAULT_DB_PATH, get_conn, init_tables
    from xshare.data.sqlite_db import DEFAULT_SQLITE_PATH, get_sqlite_conn, init_sqlite_tables

    print(f"DuckDB (OLAP): {DEFAULT_DB_PATH}")
    print("初始化 OLAP 表结构...")
    init_tables()
    duck_conn = get_conn()
    duck_tables = duck_conn.execute("SHOW TABLES").fetchall()
    print(f"已创建的 DuckDB 表 ({len(duck_tables)}):")
    for (t,) in duck_tables:
        count = duck_conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  - {t} ({count} rows)")

    print(f"\nSQLite (OLTP): {DEFAULT_SQLITE_PATH}")
    print("初始化 OLTP 表结构...")
    init_sqlite_tables()
    sqlite_conn = get_sqlite_conn()
    sqlite_tables = sqlite_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    print(f"已创建的 SQLite 表 ({len(sqlite_tables)}):")
    for (t,) in sqlite_tables:
        count = sqlite_conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  - {t} ({count} rows)")
    return 0


def _load_csv(filepath: str) -> list[dict]:
    """读取持仓 CSV（UTF-8 / UTF-8-sig）

    必填列：code, price, quantity
    可选列：name, trade_date（默认今天）, memo
    """
    records = []
    with open(filepath, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            code = row.get("code", "").strip()
            price = row.get("price", "").strip()
            quantity = row.get("quantity", "").strip()

            if not code or not price or not quantity:
                print(f"  ⚠ 第{i}行跳过：缺少必填字段 (code={code}, price={price}, quantity={quantity})")
                continue

            try:
                records.append({
                    "code": code,
                    "name": row.get("name", "").strip(),
                    "price": float(price),
                    "quantity": int(float(quantity)),
                    "trade_date": row.get("trade_date", "").strip() or date.today().isoformat(),
                    "memo": row.get("memo", "").strip() or "CSV导入",
                })
            except ValueError as e:
                print(f"  ⚠ 第{i}行跳过：数据格式错误 ({e})")

    return records


def _import_records(records: list[dict], dry_run: bool = False) -> int:
    """写入 SQLite（portfolio 表）"""
    from xshare.data.sqlite_db import get_sqlite_conn, init_sqlite_tables

    if dry_run:
        print(f"\n[预览模式] 共 {len(records)} 条记录：")
        for r in records:
            print(f"  {r['code']} {r['name']:　<6} {r['quantity']}股 × {r['price']}元 = {r['price'] * r['quantity']:.2f}元  ({r['trade_date']})")
        return 0

    init_sqlite_tables()
    conn = get_sqlite_conn()

    count = 0
    try:
        conn.execute("BEGIN")
        for r in records:
            amount = r["price"] * r["quantity"]
            conn.execute("""
                INSERT INTO portfolio (code, name, direction, trade_date, price, quantity, amount, memo)
                VALUES (?, ?, 'buy', ?, ?, ?, ?, ?)
            """, [r["code"], r["name"], r["trade_date"], r["price"], r["quantity"], amount, r["memo"]])
            count += 1
            print(f"  ✓ {r['code']} {r['name']} {r['quantity']}股 × {r['price']}元")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    print(f"\n导入完成：{count} 条记录")
    return 0


def _cmd_portfolio_import(args) -> int:
    from pathlib import Path

    filepath = args.csv_file
    if not Path(filepath).exists():
        print(f"文件不存在: {filepath}")
        return 1

    print(f"读取文件: {filepath}")
    records = _load_csv(filepath)
    if not records:
        print("未读取到有效记录")
        return 1

    print(f"读取到 {len(records)} 条有效记录")
    return _import_records(records, dry_run=args.dry_run)


def _cmd_serve(args) -> int:
    from xshare.mcp_server import run_server

    asyncio.run(run_server(token=args.tushare_token))
    return 0


def _cmd_web(args) -> int:
    import uvicorn
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    from xshare.logging_config import configure_logging
    configure_logging()
    try:
        from xshare.data.db import init_tables
        from xshare.data.sqlite_db import init_sqlite_tables
        init_tables()
        init_sqlite_tables()
    except Exception as e:
        print(f"Warning: init_tables skipped ({e})")
    print(f"XShare Web API 启动: http://{args.host}:{args.port}")
    print(f"API 文档: http://{args.host}:{args.port}/docs")
    # loop="asyncio"：避免 uvloop 在二次 Ctrl+C / 后台线程关 loop 时抛
    # "Event loop is closed"（Python 3.14 + uvloop 常见噪音）。
    uvicorn.run(
        "xshare.web_server:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level="info",
        loop="asyncio",
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xshare",
        description="XShare CLI - 数据库 / 持仓 / MCP Server / Web API 管理（同步请用 Web /sync）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # xshare db init
    p_db = sub.add_parser("db", help="数据库操作")
    db_sub = p_db.add_subparsers(dest="db_command", required=True)
    p_db_init = db_sub.add_parser("init", help="初始化 DuckDB(OLAP) + SQLite(OLTP) 表结构")
    p_db_init.set_defaults(func=_cmd_db_init)

    # xshare portfolio import
    p_port = sub.add_parser("portfolio", help="持仓数据操作")
    port_sub = p_port.add_subparsers(dest="portfolio_command", required=True)
    p_import = port_sub.add_parser("import", help="从 CSV 批量导入持仓")
    p_import.add_argument("csv_file", help="CSV 文件路径")
    p_import.add_argument("--dry-run", action="store_true", help="预览模式，不实际写入")
    p_import.set_defaults(func=_cmd_portfolio_import)

    # xshare serve
    p_serve = sub.add_parser("serve", help="运行 MCP Server")
    p_serve.add_argument(
        "--tushare-token",
        default=os.environ.get("TUSHARE_TOKEN", ""),
        help="Tushare Pro API token (也可通过 TUSHARE_TOKEN 环境变量设置)",
    )
    p_serve.set_defaults(func=_cmd_serve)

    # xshare web
    p_web = sub.add_parser("web", help="启动 Web 前端 API 服务")
    p_web.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    p_web.add_argument("--port", type=int, default=8080, help="监听端口 (默认 8080)")
    p_web.set_defaults(func=_cmd_web)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
