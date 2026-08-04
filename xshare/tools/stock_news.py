"""个股新闻查询"""

import json

from xshare.data.news import query_news


async def stock_news(args: dict) -> str:
    """查询个股相关新闻"""
    code = args.get("code")
    keyword = args.get("keyword")
    days = args.get("days", 7)

    results = query_news(code=code, keyword=keyword, days=days)

    if not results:
        return json.dumps({"news": [], "message": "未找到相关新闻"}, ensure_ascii=False)

    # 精简输出
    news_list = []
    for r in results:
        news_list.append({
            "title": r.get("title", ""),
            "time": str(r.get("publish_time", "")),
            "source": r.get("source", ""),
            "summary": (r.get("content", "") or "")[:200],
        })

    return json.dumps({"news": news_list}, ensure_ascii=False, default=str)
