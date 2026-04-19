"""技术指标计算"""

import json

from xshare.data.provider import get_provider
from xshare.indicators.technical import calculate_indicators


async def stock_indicators(args: dict) -> str:
    """计算股票技术指标"""
    code = args["code"]
    indicators = args["indicators"]
    period = args.get("period", "daily")

    df = get_provider().get_daily_history(code, days=250)

    if df.empty:
        return json.dumps({"error": f"未找到 {code} 的行情数据"}, ensure_ascii=False)

    result = calculate_indicators(df, indicators)
    result["code"] = code
    result["period"] = period
    return json.dumps(result, ensure_ascii=False, default=str)
