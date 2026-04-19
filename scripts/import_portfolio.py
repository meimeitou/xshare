#!/usr/bin/env python3
"""从 CSV 批量导入持仓数据

CSV 格式示例（UTF-8 编码）：
    code,name,price,quantity,trade_date,memo
    601899.SH,紫金矿业,15.00,1000,2025-06-01,初始化导入
    002594.SZ,比亚迪,280.00,500,,

必填列：code, price, quantity
可选列：name, trade_date（默认今天）, memo

用法：
    uv run python scripts/import_portfolio.py portfolio.csv
    uv run python scripts/import_portfolio.py portfolio.csv --dry-run   # 预览不写入
"""

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xshare.data.db import get_conn, init_tables


def load_csv(filepath: str) -> list[dict]:
    """读取 CSV 文件"""
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


def import_records(records: list[dict], dry_run: bool = False):
    """写入 DuckDB"""
    if dry_run:
        print(f"\n[预览模式] 共 {len(records)} 条记录：")
        for r in records:
            print(f"  {r['code']} {r['name']:　<6} {r['quantity']}股 × {r['price']}元 = {r['price'] * r['quantity']:.2f}元  ({r['trade_date']})")
        return

    init_tables()
    conn = get_conn()

    count = 0
    for r in records:
        amount = r["price"] * r["quantity"]
        conn.execute("""
            INSERT INTO portfolio (code, name, direction, trade_date, price, quantity, amount, memo)
            VALUES (?, ?, 'buy', ?, ?, ?, ?, ?)
        """, [r["code"], r["name"], r["trade_date"], r["price"], r["quantity"], amount, r["memo"]])
        count += 1
        print(f"  ✓ {r['code']} {r['name']} {r['quantity']}股 × {r['price']}元")

    print(f"\n导入完成：{count} 条记录")


def main():
    parser = argparse.ArgumentParser(description="从 CSV 批量导入持仓数据")
    parser.add_argument("csv_file", help="CSV 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际写入")
    args = parser.parse_args()

    filepath = args.csv_file
    if not Path(filepath).exists():
        print(f"文件不存在: {filepath}")
        sys.exit(1)

    print(f"读取文件: {filepath}")
    records = load_csv(filepath)

    if not records:
        print("未读取到有效记录")
        sys.exit(1)

    print(f"读取到 {len(records)} 条有效记录")
    import_records(records, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
