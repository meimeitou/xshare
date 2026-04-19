"""同花顺 7×24 实时新闻抓取"""

import hashlib
import re
import time
from datetime import datetime

import requests

# 同花顺 7×24 新闻 API
NEWS_API = "https://news.10jqka.com.cn/tapp/news/push/stock/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://news.10jqka.com.cn/realtimenews.html",
}

# 从新闻内容中提取股票代码的正则
STOCK_CODE_RE = re.compile(r"[（(](\d{6})[)）]|(?:代码|股票)[:：]?\s*(\d{6})")


def _extract_stock_codes(text: str) -> list[str]:
    """从文本中提取可能的股票代码"""
    matches = STOCK_CODE_RE.findall(text)
    codes = []
    for groups in matches:
        code = next((g for g in groups if g), None)
        if code:
            codes.append(code)
    return list(set(codes))


def _news_id(item: dict) -> str:
    """生成新闻唯一 ID"""
    seq = item.get("seq", "") or item.get("id", "")
    if seq:
        return f"ths_{seq}"
    raw = f"{item.get('title', '')}{item.get('ctime', '')}"
    return f"ths_{hashlib.md5(raw.encode()).hexdigest()[:16]}"


def fetch_realtime_news(page: int = 1, pagesize: int = 50) -> list[dict]:
    """
    抓取同花顺 7×24 实时新闻

    Returns:
        标准化新闻记录列表，可直接传入 save_news()
    """
    params = {
        "page": page,
        "tag": "",
        "track": "website",
        "pagesize": pagesize,
    }
    try:
        resp = requests.get(NEWS_API, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"[ths_news] 请求失败: {e}")
        return []

    items = data.get("data", {}).get("list", [])
    if not items:
        return []

    records = []
    for item in items:
        title = item.get("title", "").strip()
        content = item.get("digest", "") or item.get("content", "") or ""
        ctime = item.get("ctime", "")

        # 解析时间
        try:
            publish_time = datetime.strptime(ctime, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            publish_time = datetime.now()

        stock_codes = _extract_stock_codes(f"{title} {content}")

        records.append({
            "id": _news_id(item),
            "publish_time": publish_time,
            "source": "同花顺",
            "title": title,
            "content": content[:500],
            "stock_codes": stock_codes,
            "tags": [],
        })

    return records


def fetch_all_pages(max_pages: int = 5, delay: float = 0.5) -> list[dict]:
    """抓取多页新闻"""
    all_records = []
    seen_ids = set()

    for page in range(1, max_pages + 1):
        records = fetch_realtime_news(page=page)
        if not records:
            break

        for r in records:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                all_records.append(r)

        if page < max_pages:
            time.sleep(delay)

    return all_records
