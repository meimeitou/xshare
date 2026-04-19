"""持仓管理 - 交易流水模式"""

import json
from datetime import date

from xshare.data.db import get_conn


async def portfolio_update(args: dict) -> str:
    """记录买入/卖出/删除"""
    action = args.get("action", "buy")
    code = args["code"]
    conn = get_conn()

    if action == "delete":
        record_id = args.get("id")
        if record_id:
            conn.execute("DELETE FROM portfolio WHERE id = ?", [record_id])
        else:
            conn.execute("DELETE FROM portfolio WHERE code = ?", [code])
        return json.dumps({"success": True, "message": f"已删除 {code} 的交易记录"}, ensure_ascii=False)

    # buy / sell
    name = args.get("name", "")
    trade_date = args.get("trade_date", date.today().isoformat())
    price = args["price"]
    quantity = abs(args["quantity"])
    memo = args.get("memo", "")

    if action == "sell":
        # 校验：卖出不能超过当前持仓
        held = conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM portfolio WHERE code = ?", [code]
        ).fetchone()[0]
        if quantity > held:
            return json.dumps({
                "success": False,
                "message": f"卖出数量({quantity})超过当前持仓({held}股)",
            }, ensure_ascii=False)
        quantity = -quantity  # 卖出为负数

    amount = abs(price * quantity)
    direction = "sell" if quantity < 0 else "buy"

    # 补全名称
    if not name:
        row = conn.execute("SELECT name FROM stock_basic WHERE code = ?", [code]).fetchone()
        if row:
            name = row[0]

    conn.execute("""
        INSERT INTO portfolio (code, name, direction, trade_date, price, quantity, amount, memo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, [code, name, direction, trade_date, price, quantity, amount, memo])

    action_text = "买入" if direction == "buy" else "卖出"
    return json.dumps({
        "success": True,
        "message": f"已记录{action_text}: {name or code} {abs(quantity)}股 × {price}元 = {amount:.2f}元",
        "record": {"code": code, "name": name, "direction": direction,
                   "trade_date": trade_date, "price": price,
                   "quantity": quantity, "amount": amount},
    }, ensure_ascii=False)


async def portfolio_summary(args: dict) -> str:
    """持仓概览 + 已实现盈亏"""
    conn = get_conn()

    # 当前持仓（净数量 > 0 的标的）
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

    if not rows:
        return json.dumps({"holdings": [], "message": "当前无交易记录"}, ensure_ascii=False)

    holdings = []
    cleared = []  # 已清仓的
    total_buy_cost = 0.0
    realized_pnl = 0.0

    for code, name, net_qty, buy_amt, sell_amt, buy_qty, sell_qty in rows:
        net_qty = int(net_qty)
        buy_qty = int(buy_qty)
        sell_qty = int(sell_qty)
        avg_buy_price = buy_amt / buy_qty if buy_qty > 0 else 0

        # 已实现盈亏 = 卖出金额 - 卖出股数 × 买入均价
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
        "total_positions": len(holdings),
        "total_holding_cost": round(total_buy_cost, 2),
        "realized_pnl": round(realized_pnl, 2),
        "cleared": cleared,
        "hint": "请结合 stock_quote 获取实时价格，计算每只股票的浮动盈亏",
    }
    return json.dumps(result, ensure_ascii=False)
