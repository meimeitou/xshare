"""新闻拉取 & 存储"""

import hashlib
from datetime import datetime, timedelta


from xshare.data.db import get_conn


def _url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def save_news(records: list[dict]):
    """批量保存新闻（主键去重，已存在则跳过）"""
    if not records:
        return
    conn = get_conn()
    for r in records:
        news_id = r.get("id") or _url_hash(r.get("url", r.get("title", "")))
        conn.execute("""
            INSERT INTO news (id, publish_time, source, title, content, stock_codes, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO NOTHING
        """, [
            news_id,
            r.get("publish_time"),
            r.get("source", ""),
            r.get("title", ""),
            r.get("content", "")[:500],
            r.get("stock_codes", []),
            r.get("tags", []),
        ])


def query_news(code: str | None = None, keyword: str | None = None, days: int = 7) -> list[dict]:
    """查询新闻"""
    conn = get_conn()
    since = datetime.now() - timedelta(days=days)
    conditions = ["publish_time >= ?"]
    params: list = [since]

    if code:
        code_text = str(code).strip().upper()
        variants = [code_text]
        # 兼容入库为 6 位裸码（如 002594）与查询传入带后缀（如 002594.SZ）
        if "." in code_text:
            head = code_text.split(".", 1)[0]
            if head.isdigit() and len(head) == 6:
                variants.append(head)
        elif code_text.isdigit() and len(code_text) == 6:
            variants.extend([f"{code_text}.SZ", f"{code_text}.SH", f"{code_text}.BJ"])

        # 去重且保序
        seen: set[str] = set()
        code_variants = []
        for v in variants:
            if v not in seen:
                seen.add(v)
                code_variants.append(v)

        placeholders = " OR ".join(["list_contains(stock_codes, ?)"] * len(code_variants))
        conditions.append(f"({placeholders})")
        params.extend(code_variants)
    if keyword:
        conditions.append("(title ILIKE ? OR content ILIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])

    where = " AND ".join(conditions)
    sql = f"SELECT * FROM news WHERE {where} ORDER BY publish_time DESC LIMIT 50"
    df = conn.execute(sql, params).fetchdf()
    return df.to_dict(orient="records")


def cleanup_old_news(retain_days: int = 1):
    """清理过期新闻，默认只保留 1 天"""
    conn = get_conn()
    cutoff = datetime.now() - timedelta(days=retain_days)
    conn.execute("DELETE FROM news WHERE publish_time < ? OR publish_time IS NULL", [cutoff])
