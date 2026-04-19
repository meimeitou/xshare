"""大盘概览"""

import json
from concurrent.futures import ThreadPoolExecutor

from xshare.data.provider import get_provider


def _safe_call(fn, *args, timeout=10):
    """带超时的安全调用"""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn, *args)
        return future.result(timeout=timeout)


async def market_overview(args: dict) -> str:
    """获取大盘概览"""
    provider = get_provider()
    result = {}

    # 主要指数
    try:
        indices_list = _safe_call(provider.get_main_indices)
        indices = {}
        key_map = {"上证指数": "sh_index", "深证成指": "sz_index", "创业板指": "cyb_index", "科创50": "kc50_index"}
        for idx in indices_list:
            key = key_map.get(idx.name)
            if key:
                indices[key] = {"name": idx.name, "price": idx.price, "change_pct": idx.change_pct}
        result["indices"] = indices
    except Exception as e:
        result["indices_error"] = str(e)

    # 涨跌统计
    try:
        stats = _safe_call(provider.get_market_stats)
        result["market_stats"] = {
            "total": stats.total, "up": stats.up, "down": stats.down,
            "flat": stats.flat, "limit_up": stats.limit_up, "limit_down": stats.limit_down,
        }
    except Exception as e:
        result["market_stats_error"] = str(e)

    # 两市总成交额
    try:
        result["total_turnover_yi"] = _safe_call(provider.get_total_turnover)
    except Exception as e:
        result["turnover_error"] = str(e)

    # 板块涨跌排行
    try:
        top_up, top_down = _safe_call(provider.get_sector_rankings, 5)
        result["sector_top_up"] = [{"name": s.name, "change_pct": s.change_pct, "leader": s.leader, "leader_pct": s.leader_pct} for s in top_up]
        result["sector_top_down"] = [{"name": s.name, "change_pct": s.change_pct, "leader": s.leader, "leader_pct": s.leader_pct} for s in top_down]
    except Exception as e:
        result["sector_error"] = str(e)

    # 北向资金
    try:
        result["northbound"] = _safe_call(provider.get_northbound_flow)
    except Exception as e:
        result["northbound_error"] = str(e)

    # 涨跌幅 Top 5
    try:
        gainers, losers = _safe_call(provider.get_top_movers, 5)
        result["top_gainers"] = [{"code": m.code, "name": m.name, "price": m.price, "change_pct": m.change_pct} for m in gainers]
        result["top_losers"] = [{"code": m.code, "name": m.name, "price": m.price, "change_pct": m.change_pct} for m in losers]
    except Exception as e:
        result["movers_error"] = str(e)

    return json.dumps(result, ensure_ascii=False)
