#!/usr/bin/env python3
"""同花顺新闻定时同步脚本

用法:
  # 单次同步
  uv run python scripts/sync_news.py

  # 定时同步（每 10 分钟一次）
  uv run python scripts/sync_news.py --interval 10

  # 指定抓取页数
  uv run python scripts/sync_news.py --pages 3
"""

import argparse
import signal
import sys
import time
from datetime import datetime

# 项目根目录加入 path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from xshare.data.db import init_tables, close
from xshare.data.news import save_news, cleanup_old_news
from xshare.data.sources.ths_news import fetch_all_pages


def sync_once(max_pages: int = 5, retain_days: int = 1) -> int:
    """执行一次同步：抓取 + 入库 + 清理"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 抓取
    records = fetch_all_pages(max_pages=max_pages)
    if records:
        save_news(records)
        print(f"[{now}] 入库 {len(records)} 条新闻")
    else:
        print(f"[{now}] 未获取到新闻")

    # 清理过期（只保留 retain_days 天）
    cleanup_old_news(retain_days=retain_days)

    return len(records)


def main():
    parser = argparse.ArgumentParser(description="同花顺新闻同步")
    parser.add_argument("--pages", type=int, default=5, help="每次抓取页数 (默认 5)")
    parser.add_argument("--interval", type=int, default=0,
                        help="定时同步间隔（分钟），0 表示只执行一次")
    parser.add_argument("--retain-days", type=int, default=1,
                        help="新闻保留天数 (默认 1)")
    args = parser.parse_args()

    init_tables()

    # 优雅退出
    running = True
    def handle_signal(sig, frame):
        nonlocal running
        print("\n正在退出...")
        running = False
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    if args.interval <= 0:
        # 单次执行
        count = sync_once(max_pages=args.pages, retain_days=args.retain_days)
        close()
        print(f"完成，共 {count} 条")
        return

    # 定时循环
    print(f"启动定时同步：每 {args.interval} 分钟，保留 {args.retain_days} 天新闻")
    while running:
        try:
            sync_once(max_pages=args.pages, retain_days=args.retain_days)
        except Exception as e:
            print(f"[错误] {e}")

        # 分段 sleep，方便及时响应退出信号
        for _ in range(args.interval * 60):
            if not running:
                break
            time.sleep(1)

    close()
    print("已退出")


if __name__ == "__main__":
    main()
