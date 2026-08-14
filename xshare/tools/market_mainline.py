"""主线行情识别"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from xshare.data.db import get_conn
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


# ─── 离线三维度共振主线分析 ──────────────────────────────────────────────────


def _score_mainline_from_db(sector_top_n: int, strong_limit: int) -> dict | None:
    """离线三维度共振主线分析。数据不足时返回 None，调用方降级到实时路径。

    三维度：
    - 逻辑面：concept_board 热度/涨停数排序
    - 资金面：sector_moneyflow 主力净流入 + market_moneyflow 大盘资金
    - 情绪面：limit_list 涨停梯队 + top_list 龙虎榜
    """
    conn = get_conn()

    # 降级检测：新表是否有当日数据
    stock_daily_latest = conn.execute(
        "SELECT MAX(trade_date) FROM stock_daily"
    ).fetchone()[0]
    if stock_daily_latest is None:
        return None

    concept_latest = conn.execute(
        "SELECT MAX(trade_date) FROM concept_board"
    ).fetchone()[0]
    limit_latest = conn.execute(
        "SELECT MAX(trade_date) FROM limit_list"
    ).fetchone()[0]

    # concept_board 或 limit_list 无数据 → 降级
    if concept_latest is None or limit_latest is None:
        return None

    # ── 1. 逻辑面：概念题材热度榜 ──
    concepts = conn.execute(
        """
        SELECT code, name, pct_change, hot, zt_num, main_change,
               lead_stock, lead_stock_code, lead_stock_pct
        FROM concept_board
        WHERE trade_date = ?
        ORDER BY COALESCE(hot, 0) DESC, COALESCE(zt_num, 0) DESC, COALESCE(pct_change, 0) DESC
        LIMIT ?
        """,
        [concept_latest, sector_top_n * 3],
    ).fetchall()

    if not concepts:
        return None

    # ── 2. 资金面：板块资金净流入 ──
    sector_mf = {}
    for row in conn.execute(
        """
        SELECT code, net_amount
        FROM sector_moneyflow
        WHERE trade_date = ? AND content_type = '概念'
        """,
        [concept_latest],
    ).fetchall():
        sector_mf[row[0]] = float(row[1]) if row[1] is not None else 0.0

    # ── 3. 情绪面：涨停梯队 ──
    limit_ladder = {"total_zt": 0}
    limit_rows = conn.execute(
        """
        SELECT limit_times, COUNT(*) AS cnt
        FROM limit_list
        WHERE trade_date = ? AND limit_type = 'U'
        GROUP BY limit_times
        ORDER BY COALESCE(limit_times, 0) DESC
        """,
        [limit_latest],
    ).fetchall()
    for lt, cnt in limit_rows:
        lt_val = int(lt) if lt is not None else 0
        if lt_val >= 5:
            key = "5连+"
        elif lt_val == 4:
            key = "4连"
        elif lt_val == 3:
            key = "3连"
        elif lt_val == 2:
            key = "2连"
        else:
            key = "首板"
        limit_ladder[key] = limit_ladder.get(key, 0) + int(cnt)
        limit_ladder["total_zt"] += int(cnt)

    # ── 大盘资金流向 ──
    mkt_mf_row = conn.execute(
        """
        SELECT net_amount, buy_elg_amount
        FROM market_moneyflow
        WHERE trade_date = ?
        """,
        [concept_latest],
    ).fetchone()
    market_moneyflow = {}
    if mkt_mf_row:
        market_moneyflow = {
            "net_amount": float(mkt_mf_row[0]) if mkt_mf_row[0] is not None else 0.0,
            "buy_elg_amount": float(mkt_mf_row[1]) if mkt_mf_row[1] is not None else 0.0,
        }

    # ── 4. 三维共振主线排序 ──
    # 共振分 = RANK(net_amount) * 0.4 + RANK(zt_num) * 0.3 + RANK(hot) * 0.3
    # 用 RANK() 窗口函数一次算完
    resonance_rows = conn.execute(
        """
        WITH base AS (
            SELECT
                cb.code,
                cb.name,
                COALESCE(cb.pct_change, 0)   AS pct_change,
                COALESCE(cb.hot, 0)          AS hot,
                COALESCE(cb.zt_num, 0)       AS zt_num,
                COALESCE(cb.main_change, 0)  AS main_change,
                COALESCE(cb.lead_stock, '')  AS lead_stock,
                COALESCE(cb.lead_stock_code, '') AS lead_stock_code,
                COALESCE(cb.lead_stock_pct, 0)   AS lead_stock_pct,
                COALESCE(smf.net_amount, 0)  AS net_amount
            FROM concept_board cb
            LEFT JOIN sector_moneyflow smf
                ON smf.trade_date = cb.trade_date
               AND smf.content_type = '概念'
               AND smf.name = cb.name
            WHERE cb.trade_date = ?
        ),
        ranked AS (
            SELECT
                base.*,
                RANK() OVER (ORDER BY net_amount DESC) AS rk_mf,
                RANK() OVER (ORDER BY zt_num DESC)     AS rk_zt,
                RANK() OVER (ORDER BY hot DESC)        AS rk_hot
            FROM base
        )
        SELECT
            code, name, pct_change, hot, zt_num, net_amount,
            lead_stock, lead_stock_code, lead_stock_pct,
            ROUND(
                (SELECT COUNT(*) FROM base) - rk_mf + 1
            ) * 0.4
          + ROUND(
                (SELECT COUNT(*) FROM base) - rk_zt + 1
            ) * 0.3
          + ROUND(
                (SELECT COUNT(*) FROM base) - rk_hot + 1
            ) * 0.3 AS resonance_score
        FROM ranked
        ORDER BY resonance_score DESC
        LIMIT ?
        """,
        [concept_latest, sector_top_n],
    ).fetchall()

    mainline_sectors = []
    for row in resonance_rows:
        code, name, pct_chg, hot, zt_num, net_amt, lead, lead_code, lead_pct, score = row
        tag = "主线" if (pct_chg or 0) >= 3 else ("强势" if (pct_chg or 0) >= 2 else "观察")
        mainline_sectors.append({
            "name": name,
            "code": code,
            "change_pct": round(float(pct_chg or 0), 2),
            "hot": float(hot or 0),
            "zt_num": int(zt_num or 0),
            "net_amount": round(float(net_amt or 0) / 1e8, 4),  # 元 → 亿
            "lead_stock": lead or "",
            "leader": lead or "",
            "leader_pct": round(float(lead_pct or 0), 2),
            "resonance_score": round(float(score or 0), 2),
            "strength_tag": tag,
        })

    # ── 5. 龙头股识别 ──
    # 对每个主线概念，通过 concept_member 找成分股，JOIN limit_list + stock_moneyflow + top_list
    mainline_codes = [s["code"] for s in mainline_sectors]
    strong_stocks = []

    if mainline_codes:
        # 成分股 → 涨停 + 资金 + 龙虎榜综合
        # concept_member（概念成分股）数据相对稳定，用其自身最新日期查询，
        # 不强制与 concept_board 同日——同步时间可能错开。
        member_latest = conn.execute(
            "SELECT MAX(trade_date) FROM concept_member"
        ).fetchone()[0]
        placeholders = ",".join("?" * len(mainline_codes))
        leader_rows = conn.execute(
            f"""
            WITH members AS (
                SELECT code, name, concept_code
                FROM concept_member
                WHERE trade_date = ?
                  AND concept_code IN ({placeholders})
            ),
            limit_up AS (
                SELECT code, limit_times, close, pct_chg
                FROM limit_list
                WHERE trade_date = ? AND limit_type = 'U' AND COALESCE(limit_times, 0) >= 2
            ),
            sd AS (
                SELECT code, trade_date, close,
                       LAG(close) OVER (PARTITION BY code ORDER BY trade_date) AS prev_close
                FROM stock_daily
                WHERE trade_date <= ? AND trade_date >= (? - INTERVAL '7 days')
            ),
            sd_pct AS (
                SELECT code,
                       CASE WHEN prev_close IS NOT NULL AND prev_close != 0
                            THEN ROUND((close - prev_close) / prev_close * 100, 2)
                            ELSE 0 END AS daily_pct_chg
                FROM sd
                QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date DESC) = 1
            ),
            mf AS (
                SELECT code, net_mf_amount,
                       COALESCE(buy_elg_amount, 0) - COALESCE(sell_elg_amount, 0) AS elg_net_amount
                FROM stock_moneyflow
                WHERE trade_date = ?
            ),
            tl AS (
                SELECT code, net_amount
                FROM top_list
                WHERE trade_date = ?
            )
            SELECT
                m.code,
                m.name,
                m.concept_code,
                COALESCE(lu.limit_times, 0)  AS limit_times,
                COALESCE(sd_pct.daily_pct_chg, COALESCE(lu.pct_chg, 0)) AS pct_chg,
                COALESCE(mf.net_mf_amount, 0) AS net_mf_amount,
                COALESCE(mf.elg_net_amount, 0) AS elg_net_amount,
                COALESCE(tl.net_amount, 0)    AS top_list_net
            FROM members m
            LEFT JOIN limit_up lu ON lu.code = m.code
            LEFT JOIN sd_pct ON sd_pct.code = m.code
            LEFT JOIN mf ON mf.code = m.code
            LEFT JOIN tl ON tl.code = m.code
            WHERE COALESCE(lu.limit_times, 0) >= 2
               OR COALESCE(mf.net_mf_amount, 0) > 0
               OR COALESCE(tl.net_amount, 0) > 0
            ORDER BY
                COALESCE(lu.limit_times, 0) DESC,
                COALESCE(mf.net_mf_amount, 0) DESC
            LIMIT ?
            """,
            [member_latest, *mainline_codes, limit_latest, concept_latest, concept_latest, concept_latest, limit_latest, strong_limit * 3],
        ).fetchall()

        # 龙头评分 = 连板数×30% + 主力净流入排名×40% + 龙虎榜净买入排名×30%
        if leader_rows:
            # 计算排名（行内已按 limit_times DESC, net_mf DESC 排序）
            n = len(leader_rows)
            concept_name_map = {s["code"]: s["name"] for s in mainline_sectors}
            for i, row in enumerate(leader_rows):
                code, name, concept_code, limit_times, pct_chg, net_mf, elg_net, top_net = row
                # 排名分：越靠前分越高
                rank_mf = n - i  # 简化：用位置作排名代理
                rank_tl = n - i
                score = (
                    int(limit_times or 0) * 30
                    + rank_mf * 0.4
                    + rank_tl * 0.3
                )
                strong_stocks.append({
                    "code": code,
                    "name": name,
                    "concept": concept_name_map.get(concept_code, ""),
                    "limit_times": int(limit_times or 0),
                    "change_pct": round(float(pct_chg or 0), 2),
                    "net_mf_amount": round(float(net_mf or 0) / 1e4, 2),  # 万元 → 亿元
                    "elg_net_amount": round(float(elg_net or 0) / 1e4, 2),  # 万元 → 亿元
                    "top_list_net": round(float(top_net or 0) / 1e8, 4),  # 元 → 亿
                    "score": round(float(score), 2),
                })

            strong_stocks.sort(key=lambda x: x["score"], reverse=True)
            # 同一股票可能隶属多个概念，按 code 去重保留最高分
            seen = set()
            strong_stocks = [s for s in strong_stocks if s["code"] not in seen and not seen.add(s["code"])]
            strong_stocks = strong_stocks[:strong_limit]

    # ── 市场阶段判断 ──
    total_zt = limit_ladder.get("total_zt", 0)
    mkt_net = market_moneyflow.get("net_amount", 0)
    if total_zt > 50 and mkt_net > 0:
        market_phase = "情绪高潮（涨停潮+资金流入）"
    elif total_zt > 20 and mkt_net > 0:
        market_phase = "情绪回暖（涨停活跃+资金流入）"
    elif total_zt < 10 and mkt_net < 0:
        market_phase = "情绪退潮（涨停稀少+资金流出）"
    else:
        market_phase = "分化震荡（关注主线板块）"

    mainline_direction = (
        "、".join(s["name"] for s in mainline_sectors[:3])
        if mainline_sectors else "暂无明显主线"
    )

    return {
        "market_phase": market_phase,
        "mainline_direction": mainline_direction,
        "mainline_sectors": mainline_sectors,
        "limit_ladder": limit_ladder,
        "market_moneyflow": market_moneyflow,
        "strong_stocks": strong_stocks,
        "data_date": str(concept_latest),
        "market_snapshot": {
            "latest_date": str(concept_latest),
            "limit_latest_date": str(limit_latest),
        },
    }


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


def _read_mainline_cache() -> dict | None:
    """读取 mainline_cache 最新一行。无缓存返回 None。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT trade_date, result_json, cached_at FROM mainline_cache ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    trade_date, result_json, cached_at = row
    try:
        result = json.loads(result_json)
    except (json.JSONDecodeError, TypeError):
        return None
    result["data_source"] = "offline_3d_resonance"
    result["methodology"] = "资金+情绪+逻辑三维共振"
    result["cached_at"] = str(cached_at) if cached_at else None
    result["data_date"] = str(trade_date)
    return result


async def market_mainline(args: dict) -> str:
    """识别市场主线方向与强势股票"""
    sector_top_n = int(args.get("sector_top_n", 8))
    strong_limit = int(args.get("strong_limit", 10))

    # 优先读 mainline_cache（定时任务预算结果），命中则直接返回
    cached = await asyncio.to_thread(_read_mainline_cache)
    if cached is not None:
        return json.dumps(to_json_safe(cached), ensure_ascii=False, default=str)

    provider = get_provider()

    # 离线三维度共振路径（优先：概念题材 + 资金 + 涨停梯队）
    db_result = await asyncio.to_thread(_score_mainline_from_db, sector_top_n, strong_limit)
    if db_result is not None:
        db_result["data_source"] = "offline_3d_resonance"
        db_result["methodology"] = "资金+情绪+逻辑三维共振"
        return json.dumps(to_json_safe(db_result), ensure_ascii=False, default=str)

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
        result["data_date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
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
