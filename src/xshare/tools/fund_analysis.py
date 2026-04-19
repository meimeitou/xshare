"""基金绩效分析"""

import json

from xshare.data.provider import get_provider, DataFetchError


async def fund_analysis(args: dict) -> str:
    """基金绩效分析"""
    code = args["code"]
    period = args.get("period", "1y")

    try:
        df = get_provider().get_fund_nav(code)
    except DataFetchError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    if df.empty:
        return json.dumps({"error": f"未找到基金 {code} 的净值数据"}, ensure_ascii=False)

    # 基础绩效指标
    df = df.sort_values("nav_date")
    nav_series = df["nav"].astype(float)

    total_return = (nav_series.iloc[-1] / nav_series.iloc[0] - 1) * 100
    daily_returns = nav_series.pct_change().dropna()
    annual_return = daily_returns.mean() * 252 * 100
    volatility = daily_returns.std() * (252 ** 0.5) * 100
    sharpe = (annual_return - 2.0) / volatility if volatility > 0 else 0

    # 最大回撤
    cummax = nav_series.cummax()
    drawdowns = (nav_series - cummax) / cummax
    max_drawdown = drawdowns.min() * 100

    result = {
        "code": code,
        "period": period,
        "total_return_pct": round(total_return, 2),
        "annual_return_pct": round(annual_return, 2),
        "volatility_pct": round(volatility, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "sharpe_ratio": round(sharpe, 2),
        "data_points": len(nav_series),
    }
    return json.dumps(result, ensure_ascii=False)
