"""条件筛选"""

import json

from xshare.data.db import get_conn


async def stock_screen(args: dict) -> str:
    """按条件筛选股票"""
    filters = args["filters"]
    sector = args.get("sector")
    limit = args.get("limit", 20)

    conn = get_conn()

    # 构建 SQL —— 联合 stock_basic 和最新 stock_finance
    conditions = []
    params = []

    for f in filters:
        field = f["field"]
        op = f["op"]
        value = f["value"]
        # 白名单校验字段名防止注入
        allowed_fields = {"pe", "pb", "roe", "revenue_yoy", "profit_yoy", "revenue", "net_profit"}
        if field not in allowed_fields:
            return json.dumps({"error": f"不支持的筛选字段: {field}"}, ensure_ascii=False)
        allowed_ops = {"<", ">", "<=", ">=", "==", "!="}
        if op not in allowed_ops:
            return json.dumps({"error": f"不支持的操作符: {op}"}, ensure_ascii=False)
        sql_op = "=" if op == "==" else op
        conditions.append(f"f.{field} {sql_op} ?")
        params.append(value)

    if sector:
        conditions.append("b.industry = ?")
        params.append(sector)

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)

    sql = f"""
        SELECT b.code, b.name, b.industry, f.pe, f.pb, f.roe
        FROM stock_basic b
        JOIN (
            SELECT code, pe, pb, roe, revenue_yoy, profit_yoy, revenue, net_profit,
                   ROW_NUMBER() OVER (PARTITION BY code ORDER BY end_date DESC) as rn
            FROM stock_finance
        ) f ON b.code = f.code AND f.rn = 1
        WHERE {where_clause}
        LIMIT ?
    """

    rows = conn.execute(sql, params).fetchall()
    columns = ["code", "name", "industry", "pe", "pb", "roe"]
    results = [dict(zip(columns, row)) for row in rows]

    return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False)
