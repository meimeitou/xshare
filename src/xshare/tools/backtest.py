"""策略回测"""

import json


from xshare.data.db import get_conn


async def backtest_run(args: dict) -> str:
    """简易策略回测"""
    strategy = args["strategy"]
    target = args["target"]
    start_date = args["start_date"]
    end_date = args["end_date"]

    conn = get_conn()
    df = conn.execute(
        "SELECT * FROM stock_daily WHERE code = ? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
        [target, start_date, end_date],
    ).fetchdf()

    if df.empty:
        return json.dumps({"error": f"未找到 {target} 在指定区间的行情数据"}, ensure_ascii=False)

    # TODO: 根据 strategy 定义执行回测逻辑
    # MVP 阶段先返回基础 buy-and-hold 基准

    close = df["close"].astype(float)
    total_return = (close.iloc[-1] / close.iloc[0] - 1) * 100
    days = len(close)
    annual_return = total_return * 252 / days if days > 0 else 0

    cummax = close.cummax()
    max_drawdown = ((close - cummax) / cummax).min() * 100

    result = {
        "strategy": strategy.get("name", "buy_and_hold"),
        "target": target,
        "start_date": start_date,
        "end_date": end_date,
        "total_return_pct": round(total_return, 2),
        "annual_return_pct": round(annual_return, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "trade_days": days,
        "trades": [],
        "disclaimer": "⚠️ 历史业绩不代表未来表现，回测结果仅供参考。",
    }
    return json.dumps(result, ensure_ascii=False)
