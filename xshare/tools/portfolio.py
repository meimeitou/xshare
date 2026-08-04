"""持仓管理 - 交易流水模式"""

import json
from datetime import date

from xshare.data.db import get_conn as get_duckdb_conn
from xshare.data.sqlite_db import get_sqlite_conn


async def portfolio_update(args: dict) -> str:
    """记录买入/卖出/删除"""
    action = args.get("action", "buy")
    code = args.get("code")
    conn = get_sqlite_conn()

    if action == "delete":
        record_id = args.get("id")
        if record_id is not None:
            exists = conn.execute(
                "SELECT 1 FROM portfolio WHERE id = ?", [record_id]
            ).fetchone()
            if not exists:
                return json.dumps({
                    "success": False,
                    "message": f"未找到交易记录 #{record_id}",
                    "retry_same_args": False,
                }, ensure_ascii=False)
            conn.execute("DELETE FROM portfolio WHERE id = ?", [record_id])
            return json.dumps(
                {"success": True, "message": f"已删除交易记录 #{record_id}"},
                ensure_ascii=False,
            )
        if not code:
            return json.dumps({
                "success": False,
                "message": "delete 需要提供 id 或 code",
                "retry_same_args": False,
            }, ensure_ascii=False)
        conn.execute("DELETE FROM portfolio WHERE code = ?", [code])
        return json.dumps({"success": True, "message": f"已删除 {code} 的交易记录"}, ensure_ascii=False)

    # buy / sell
    if not code:
        return json.dumps({
            "success": False,
            "message": "buy/sell 需要提供 code",
            "retry_same_args": False,
        }, ensure_ascii=False)

    name = args.get("name", "")
    trade_date = args.get("trade_date", date.today().isoformat())
    price = args["price"]
    quantity = abs(args["quantity"])
    memo = args.get("memo", "")

    if action == "sell":
        held = conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM portfolio WHERE code = ?", [code]
        ).fetchone()[0]
        if quantity > held:
            return json.dumps({
                "success": False,
                "message": f"卖出数量({quantity})超过当前持仓({held}股)",
            }, ensure_ascii=False)
        quantity = -quantity

    amount = abs(price * quantity)
    direction = "sell" if quantity < 0 else "buy"

    if not name:
        row = get_duckdb_conn().execute(
            "SELECT name FROM stock_basic WHERE code = ?", [code]
        ).fetchone()
        if row:
            name = row[0]

    cur = conn.execute("""
        INSERT INTO portfolio (code, name, direction, trade_date, price, quantity, amount, memo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
    """, [code, name, direction, trade_date, price, quantity, amount, memo])
    record_id = int(cur.fetchone()[0])

    action_text = "买入" if direction == "buy" else "卖出"
    return json.dumps({
        "success": True,
        "message": f"已记录{action_text}: {name or code} {abs(quantity)}股 × {price}元 = {amount:.2f}元",
        "record": {
            "id": record_id,
            "code": code,
            "name": name,
            "direction": direction,
            "trade_date": trade_date,
            "price": price,
            "quantity": quantity,
            "amount": amount,
        },
    }, ensure_ascii=False)


async def portfolio_summary(args: dict) -> str:
    """持仓概览 + 交易流水（供 Web UI）+ 已实现盈亏"""
    conn = get_sqlite_conn()

    rows = conn.execute("""
        SELECT code, name,
               SUM(quantity) as net_qty,
               SUM(CASE WHEN direction='buy' THEN amount ELSE 0 END) as buy_amount,
               SUM(CASE WHEN direction='sell' THEN amount ELSE 0 END) as sell_amount,
               SUM(CASE WHEN direction='buy' THEN quantity ELSE 0 END) as buy_qty,
               SUM(CASE WHEN direction='sell' THEN -quantity ELSE 0 END) as sell_qty
        FROM portfolio
        GROUP BY code, name
        ORDER BY buy_amount DESC
    """).fetchall()

    trade_rows = conn.execute("""
        SELECT id, code, name, direction, trade_date, price, quantity, amount, memo
        FROM portfolio
        ORDER BY id DESC
        LIMIT 200
    """).fetchall()

    records = [
        {
            "id": int(r[0]),
            "code": r[1],
            "name": r[2] or r[1],
            "direction": r[3],
            "trade_date": str(r[4]),
            "price": float(r[5]) if r[5] is not None else 0.0,
            "quantity": int(r[6]) if r[6] is not None else 0,
            "amount": float(r[7]) if r[7] is not None else 0.0,
            "memo": r[8] or "",
        }
        for r in trade_rows
    ]

    if not rows and not records:
        return json.dumps({
            "holdings": [],
            "records": [],
            "total_positions": 0,
            "total_holding_cost": 0.0,
            "total_cost": 0.0,
            "realized_pnl": 0.0,
            "message": "当前无交易记录",
        }, ensure_ascii=False)

    holdings = []
    cleared = []
    total_buy_cost = 0.0
    realized_pnl = 0.0

    for code, name, net_qty, buy_amt, sell_amt, buy_qty, sell_qty in rows:
        net_qty = int(net_qty)
        buy_qty = int(buy_qty)
        sell_qty = int(sell_qty)
        avg_buy_price = buy_amt / buy_qty if buy_qty > 0 else 0

        if sell_qty > 0:
            realized = sell_amt - sell_qty * avg_buy_price
            realized_pnl += realized

        if net_qty > 0:
            holding_cost = net_qty * avg_buy_price
            holdings.append({
                "code": code,
                "name": name or code,
                "quantity": net_qty,
                "avg_cost": round(avg_buy_price, 3),
                "holding_cost": round(holding_cost, 2),
            })
            total_buy_cost += holding_cost
        elif net_qty == 0 and sell_qty > 0:
            cleared.append({"code": code, "name": name or code})

    result = {
        "holdings": holdings,
        "records": records,
        "total_positions": len(holdings),
        "positions": len(holdings),
        "total_holding_cost": round(total_buy_cost, 2),
        "total_cost": round(total_buy_cost, 2),
        "realized_pnl": round(realized_pnl, 2),
        "cleared": cleared,
        "hint": "请结合 stock_quote 获取实时价格，计算每只股票的浮动盈亏",
    }
    return json.dumps(result, ensure_ascii=False)
