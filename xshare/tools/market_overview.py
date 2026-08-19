"""大盘概览"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from xshare.data.provider import get_provider
from xshare.utils import safe_call, to_json_safe


def _call_provider(provider, method: str, *args):
    return safe_call(getattr(provider, method), *args, timeout=10)


# 复用一个全局线程池，避免每个 safe_call 内部各建一个 max_workers=1 的池。
_overview_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mkt-overview")


async def _run(fn, *args):
    """在线程池里跑同步 provider 调用，返回 (value, exc)。"""
    fut = _overview_pool.submit(fn, *args)
    try:
        return await asyncio.wrap_future(fut), None
    except Exception as exc:  # noqa: BLE001
        return None, exc


async def _gather_overview(provider):
    """并行拉取 6 路数据，互不阻塞。"""
    indices_task = _run(_call_provider, provider, "get_main_indices")
    stats_task = _run(_call_provider, provider, "get_market_stats")
    turnover_task = _run(_call_provider, provider, "get_total_turnover")
    sector_task = _run(_call_provider, provider, "get_sector_rankings", 5)
    north_task = _run(_call_provider, provider, "get_northbound_flow")
    movers_task = _run(_call_provider, provider, "get_top_movers", 5)
    return await asyncio.gather(
        indices_task, stats_task, turnover_task, sector_task, north_task, movers_task
    )


def _build_indices(indices_list) -> dict:
    key_map = {"上证指数": "sh_index", "深证成指": "sz_index", "创业板指": "cyb_index", "科创50": "kc50_index"}
    indices = {}
    for idx in indices_list:
        key = key_map.get(idx.name)
        if key:
            indices[key] = {"name": idx.name, "price": idx.price, "change_pct": idx.change_pct}
    return indices


def _build_movers(gainers, losers) -> tuple[list, list]:
    g = [{"code": m.code, "name": m.name, "price": m.price, "change_pct": m.change_pct} for m in gainers]
    l = [{"code": m.code, "name": m.name, "price": m.price, "change_pct": m.change_pct} for m in losers]
    return g, l


def _build_sectors(top_up, top_down) -> tuple[list, list]:
    up = [{"name": s.name, "change_pct": s.change_pct, "leader": s.leader, "leader_pct": s.leader_pct} for s in top_up]
    down = [{"name": s.name, "change_pct": s.change_pct, "leader": s.leader, "leader_pct": s.leader_pct} for s in top_down]
    return up, down



def _mark_northbound_stale(northbound: dict) -> None:
    """标记北向资金是否为非当日数据（盘中无实时来源，回退昨日）。

    2024-08-19 起东财停止披露北向资金盘中实时数据，Tushare moneyflow_hsgt
    为日终接口，盘中取不到当日值。当 northbound.date != 今天时加 is_stale/note，
    让前端与 AI 显式提示用户展示的是前一交易日数据。
    """
    if not isinstance(northbound, dict):
        return
    today = datetime.now().strftime("%Y%m%d")
    nb_date = str(northbound.get("date", "")).replace("-", "")
    northbound["is_stale"] = nb_date != today
    if northbound["is_stale"]:
        northbound["note"] = "盘中无北向资金实时数据，展示前一交易日日终数据"

async def market_overview(args: dict) -> str:
    """获取大盘概览（6 路数据并行拉取，单路失败不影响其它字段）。"""
    provider = get_provider()
    result = {
        "snapshot_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_policy": "realtime=akshare_only;history=duckdb;fallback=field_error",
    }

    (indices_list, e1), (stats, e2), (turnover, e3), (sectors, e4), (northbound, e5), (movers, e6) = (
        await _gather_overview(provider)
    )

    if e1 is None:
        result["indices"] = _build_indices(indices_list)
    else:
        result["indices_error"] = str(e1)

    if e2 is None:
        result["market_stats"] = {
            "total": stats.total, "up": stats.up, "down": stats.down,
            "flat": stats.flat, "limit_up": stats.limit_up, "limit_down": stats.limit_down,
        }
    else:
        result["market_stats_error"] = str(e2)

    if e3 is None:
        result["total_turnover_yi"] = turnover
    else:
        result["turnover_error"] = str(e3)

    if e4 is None:
        up, down = _build_sectors(sectors[0], sectors[1])
        result["sector_top_up"] = up
        result["sector_top_down"] = down
    else:
        result["sector_error"] = str(e4)
    if e5 is None:
        _mark_northbound_stale(northbound)
        result["northbound"] = northbound
    else:
        result["northbound_error"] = str(e5)
    if e6 is None:
        g, l = _build_movers(movers[0], movers[1])
        result["top_gainers"] = g
        result["top_losers"] = l
    else:
        result["movers_error"] = str(e6)

    return json.dumps(to_json_safe(result), ensure_ascii=False, default=str)
