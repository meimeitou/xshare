"""主线行情识别"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

from xshare.data.provider import get_provider
from xshare.indicators.technical import calculate_indicators
from xshare.utils import safe_call, to_json_safe


# 用于并发拉取个股日线 + 指标计算。akshare/tushare 调用本身在线程池里跑，
# 这里只是把串行的 N 次循环并发提交。
_mainline_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mkt-mainline")


def _calc_market_phase(indices: dict, market_stats: dict, northbound: dict) -> str:
    """根据指数、涨跌家数、北向资金判断市场阶段"""
    changes = []
    for v in indices.values():
        if isinstance(v, dict) and v.get("change_pct") is not None:
            changes.append(float(v["change_pct"]))
    avg_index_change = sum(changes) / len(changes) if changes else 0.0

    total = int(market_stats.get("total", 0) or 0)
    up = int(market_stats.get("up", 0) or 0)
    down = int(market_stats.get("down", 0) or 0)
    breadth = ((up - down) / total * 100) if total > 0 else 0.0

    north_total = float(northbound.get("total", 0) or 0)

    if avg_index_change >= 1.0 and breadth >= 10 and north_total > 0:
        return "风险偏好上行（主升）"
    if avg_index_change <= -1.0 and breadth <= -10 and north_total < 0:
        return "风险偏好下降（退潮）"
    if avg_index_change > 0 and breadth > 0:
        return "震荡偏强（结构性轮动）"
    if avg_index_change < 0 and breadth < 0:
        return "震荡偏弱（防守为主）"
    return "分化震荡（关注主线板块）"


def _normalize_code(code: str) -> str:
    """统一代码格式（去掉交易所后缀）"""
    return str(code).split(".")[0]


def _infer_mainline_sectors_from_movers(provider, sector_top_n: int) -> list[dict]:
    """板块接口不可用时，基于涨幅榜 + 股票行业字段推断主线板块"""
    gainers, _ = safe_call(provider.get_top_movers, 80)
    stock_list = safe_call(provider.get_stock_list)

    if stock_list is None or getattr(stock_list, "empty", True):
        return []
    if "code" not in stock_list.columns or "industry" not in stock_list.columns:
        return []

    industry_map = {}
    for _, row in stock_list.iterrows():
        industry = row.get("industry")
        if not industry:
            continue
        industry_map[_normalize_code(row.get("code"))] = str(industry)

    bucket = {}
    for mover in gainers:
        industry = industry_map.get(_normalize_code(mover.code))
        if not industry:
            continue
        data = bucket.setdefault(
            industry,
            {
                "name": industry,
                "sum_change": 0.0,
                "count": 0,
                "leader": mover.name,
                "leader_pct": float(mover.change_pct),
            },
        )
        change_pct = float(mover.change_pct)
        data["sum_change"] += change_pct
        data["count"] += 1
        if change_pct > data["leader_pct"]:
            data["leader"] = mover.name
            data["leader_pct"] = change_pct

    sectors = []
    for _, v in bucket.items():
        if v["count"] <= 0:
            continue
        avg_change = v["sum_change"] / v["count"]
        tag = "主线" if avg_change >= 3 else ("强势" if avg_change >= 2 else "观察")
        sectors.append(
            {
                "name": v["name"],
                "change_pct": round(avg_change, 2),
                "leader": v["leader"],
                "leader_pct": round(v["leader_pct"], 2),
                "strength_tag": tag,
            }
        )

    sectors.sort(key=lambda x: x["change_pct"], reverse=True)
    return sectors[:sector_top_n]


async def _run_sync(fn, *args):
    """同步 provider 调用丢线程池并发执行。"""
    fut = _mainline_pool.submit(fn, *args)
    return await asyncio.wrap_future(fut)


async def _run_scoring(score_fn, mover):
    """把同步的单股评分丢进线程池并发执行。"""
    fut = _mainline_pool.submit(score_fn, mover)
    try:
        return await asyncio.wrap_future(fut)
    except Exception:  # noqa: BLE001
        return None


async def market_mainline(args: dict) -> str:
    """识别市场主线方向与强势股票"""
    provider = get_provider()
    sector_top_n = int(args.get("sector_top_n", 8))
    strong_limit = int(args.get("strong_limit", 10))

    result = {
        "market_phase": "",
        "mainline_direction": "",
        "mainline_sectors": [],
        "strong_stocks": [],
        "sector_data_source": "provider",
        "methodology": "板块接口排行",
        "notes": [],
    }

    try:
        # 市场总体状态 — 并行拉取 4 路快照（共享 spot 缓存，并行可省串行开销）
        indices_list, stats, northbound, turnover = await asyncio.gather(
            _run_sync(safe_call, provider.get_main_indices),
            _run_sync(safe_call, provider.get_market_stats),
            _run_sync(safe_call, provider.get_northbound_flow),
            _run_sync(safe_call, provider.get_total_turnover),
        )

        indices = {idx.name: {"price": idx.price, "change_pct": idx.change_pct} for idx in indices_list}
        market_stats = {
            "total": stats.total,
            "up": stats.up,
            "down": stats.down,
            "flat": stats.flat,
            "limit_up": stats.limit_up,
            "limit_down": stats.limit_down,
        }

        result["market_phase"] = _calc_market_phase(indices, market_stats, northbound)
        result["market_snapshot"] = {
            "indices": indices,
            "market_stats": market_stats,
            "northbound": northbound,
            "total_turnover_yi": turnover,
        }

        # 主线板块（优先板块接口，失败则行业推断降级）
        mainline_sectors = []
        try:
            sector_tuple = await _run_sync(safe_call, provider.get_sector_rankings, sector_top_n)
            top_up, _ = sector_tuple
            for s in top_up:
                tag = "主线" if s.change_pct >= 3 else ("强势" if s.change_pct >= 2 else "观察")
                mainline_sectors.append({
                    "name": s.name,
                    "change_pct": float(s.change_pct),
                    "leader": s.leader,
                    "leader_pct": float(s.leader_pct),
                    "strength_tag": tag,
                })
        except Exception as e:
            result["sector_error"] = str(e)
            result["sector_data_source"] = "fallback_top_movers"
            result["methodology"] = "涨幅榜+行业映射推断"
            result["notes"].append("板块接口不可用，已降级为涨幅榜与行业映射推断，结果与概念题材口径可能存在偏差")
            try:
                mainline_sectors = _infer_mainline_sectors_from_movers(provider, sector_top_n)
            except Exception as fallback_e:
                result["sector_fallback_error"] = str(fallback_e)

        result["mainline_sectors"] = mainline_sectors
        result["mainline_direction"] = "、".join([s["name"] for s in mainline_sectors[:3]]) if mainline_sectors else "暂无明显主线"
        if result["sector_data_source"] == "provider":
            result["notes"].append("当前主线按数据源板块口径计算，和资讯平台按概念叙事整理的主线可能不完全一致")

        # 强势股：涨幅榜 + 技术形态过滤
        movers_tuple = await _run_sync(safe_call, provider.get_top_movers, max(strong_limit * 2, 20))
        gainers, _ = movers_tuple

        def _score_one(mover):
            """单只股票：拉日线 + 算指标 + 评分。失败返回 None。"""
            try:
                hist = safe_call(provider.get_daily_history, mover.code, None, None, 140)
                if hist is None or len(hist) < 60:
                    return None
                ind = calculate_indicators(hist, ["TREND", "VOL_MA", "NINE_TURN"])
                trend = ind.get("trend", {})
                vol_ma = ind.get("vol_ma", {})
                nine = ind.get("nine_turn", {})

                score = float(mover.change_pct)
                phase = str(trend.get("phase", ""))
                arrangement = str(trend.get("arrangement", ""))
                vol_ratio = float(vol_ma.get("vol_ratio", 0) or 0)
                signal = str(nine.get("signal", ""))

                if "上升" in phase:
                    score += 2
                if arrangement == "多头排列":
                    score += 1
                if vol_ratio >= 1.2:
                    score += 1
                if signal == "九转见顶":
                    score -= 1

                return {
                    "code": mover.code,
                    "name": mover.name,
                    "price": float(mover.price),
                    "change_pct": float(mover.change_pct),
                    "trend_phase": phase,
                    "arrangement": arrangement,
                    "vol_ratio": round(vol_ratio, 2),
                    "nine_turn_signal": signal,
                    "score": round(score, 2),
                }
            except Exception:
                # 单只股票失败不影响整体输出
                return None

        # 并发拉取 + 评分，避免 N 只股票串行阻塞（首屏最大单点延迟）
        scored = await asyncio.gather(
            *(_run_scoring(_score_one, m) for m in gainers)
        )
        candidates = [c for c in scored if c is not None]

        candidates.sort(key=lambda x: x["score"], reverse=True)
        result["strong_stocks"] = candidates[:strong_limit]

    except Exception as e:
        result["error"] = str(e)
        result["retry_same_args"] = False

    return json.dumps(to_json_safe(result), ensure_ascii=False, default=str)
