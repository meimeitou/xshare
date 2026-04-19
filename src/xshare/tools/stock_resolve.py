"""股票代码模糊匹配"""

import json

from xshare.data.db import get_conn


async def stock_resolve(args: dict) -> str:
    """模糊匹配股票代码，支持名称、代码片段"""
    query = args["query"].strip()
    conn = get_conn()

    # 精确匹配代码
    rows = conn.execute(
        "SELECT code, name FROM stock_basic WHERE code ILIKE ? OR name ILIKE ? LIMIT 10",
        [f"%{query}%", f"%{query}%"],
    ).fetchall()

    if not rows:
        return json.dumps({"matches": [], "message": f"未找到匹配: {query}"}, ensure_ascii=False)

    matches = [{"code": r[0], "name": r[1]} for r in rows]
    return json.dumps({"matches": matches}, ensure_ascii=False)
