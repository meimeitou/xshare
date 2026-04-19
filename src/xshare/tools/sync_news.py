"""同花顺新闻同步工具（供 MCP 调用）"""

import json

from xshare.data.db import init_tables
from xshare.data.news import save_news, cleanup_old_news
from xshare.data.sources.ths_news import fetch_all_pages


async def sync_news(args: dict) -> str:
    """同步同花顺 7×24 新闻到本地数据库"""
    pages = args.get("pages", 3)
    retain_days = args.get("retain_days", 1)

    # 限制范围防止滥用
    pages = max(1, min(pages, 10))
    retain_days = max(1, min(retain_days, 30))

    init_tables()
    records = fetch_all_pages(max_pages=pages)

    if records:
        save_news(records)

    cleanup_old_news(retain_days=retain_days)

    return json.dumps({
        "synced": len(records),
        "retain_days": retain_days,
        "message": f"已同步 {len(records)} 条新闻，保留最近 {retain_days} 天数据",
    }, ensure_ascii=False)
