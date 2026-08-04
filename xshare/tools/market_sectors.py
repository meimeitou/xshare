"""板块涨跌排行（细粒度接口）

从 market_overview 拆出的轻量接口，只返回板块涨跌排行，供前端独立 SWR
加载。复用 provider 的 30s TTL 缓存，避免与 overview 重复打 HTTP。
"""

import json

from xshare.data.provider import get_provider
from xshare.utils import safe_call, to_json_safe


async def market_sectors(args: dict) -> str:
    """获取板块涨跌排行 Top N。"""
    provider = get_provider()
    top_n = int(args.get("top_n", 5))
    result: dict = {}
    try:
        top_up, top_down = safe_call(provider.get_sector_rankings, top_n, timeout=10)
        result["sector_top_up"] = [
            {"name": s.name, "change_pct": s.change_pct, "leader": s.leader, "leader_pct": s.leader_pct}
            for s in top_up
        ]
        result["sector_top_down"] = [
            {"name": s.name, "change_pct": s.change_pct, "leader": s.leader, "leader_pct": s.leader_pct}
            for s in top_down
        ]
    except Exception as e:
        result["error"] = str(e)
        result["retry_same_args"] = False
    return json.dumps(to_json_safe(result), ensure_ascii=False, default=str)
