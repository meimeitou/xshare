"""涨跌幅榜（细粒度接口）

从 market_overview 拆出的轻量接口，只返回涨跌幅 Top N，供前端独立 SWR
加载，避免被 overview 的板块/北向等重数据拖累。
"""

import json

from xshare.data.provider import get_provider
from xshare.utils import safe_call, to_json_safe


async def market_top_movers(args: dict) -> str:
    """获取涨跌幅榜 Top N。"""
    provider = get_provider()
    top_n = int(args.get("top_n", 5))
    result: dict = {}
    try:
        gainers, losers = safe_call(provider.get_top_movers, top_n, timeout=10)
        result["top_gainers"] = [
            {"code": m.code, "name": m.name, "price": m.price, "change_pct": m.change_pct}
            for m in gainers
        ]
        result["top_losers"] = [
            {"code": m.code, "name": m.name, "price": m.price, "change_pct": m.change_pct}
            for m in losers
        ]
    except Exception as e:
        result["error"] = str(e)
        result["retry_same_args"] = False
    return json.dumps(to_json_safe(result), ensure_ascii=False, default=str)
