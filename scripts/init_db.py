#!/usr/bin/env python3
"""初始化本地 DuckDB 数据库"""

import sys
from pathlib import Path

# 确保可以 import xshare
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xshare.data.db import get_conn, init_tables, DEFAULT_DB_PATH


def main():
    print(f"数据库路径: {DEFAULT_DB_PATH}")
    print("初始化表结构...")
    init_tables()
    print("完成。")

    # 验证
    conn = get_conn()
    tables = conn.execute("SHOW TABLES").fetchall()
    print(f"\n已创建的表 ({len(tables)}):")
    for (t,) in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  - {t} ({count} rows)")


if __name__ == "__main__":
    main()
