"""趋势扫描 — 识别多日走趋势的股票与行业

评分维度（满分约 50）：
  momentum_20d  × 0.3   — 20 日涨幅（动量）
  momentum_60d  × 0.2   — 60 日涨幅（中期趋势）
  ma_score      × 8     — 均线多头排列度（0~3分）
  vol_expansion × 15    — 量能放大倍数（vol_ma10 / vol_ma30 超过 1 的部分）
  above_ma20    × 5     — 收盘价在 MA20 上方

数据优先级：
  1. 本地 stock_daily 表（SQL 窗口函数，毫秒级）
  2. Provider 实时拉取（取今日涨幅榜 top N，逐只拉 60 日历史，兜底）
"""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from xshare.data.db import get_conn
from xshare.data.provider import get_provider
from xshare.indicators.technical import calculate_indicators
from xshare.utils import safe_call

logger = logging.getLogger(__name__)

# ─── SQL 路径（本地 stock_daily 有数据时使用）────────────────────────────────

_SCORE_SQL = """
WITH windowed AS (
    SELECT
        code,
        trade_date,
        close,
        volume,
        AVG(close)  OVER w5  AS ma5,
        AVG(close)  OVER w10 AS ma10,
        AVG(close)  OVER w20 AS ma20,
        AVG(close)  OVER w60 AS ma60,
        AVG(volume) OVER w10 AS vol_ma10,
        AVG(volume) OVER w30 AS vol_ma30,
        FIRST_VALUE(close) OVER w20 AS close_20d_ago,
        FIRST_VALUE(close) OVER w60 AS close_60d_ago,
        ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date DESC) AS rn
    FROM stock_daily
    WINDOW
        w5  AS (PARTITION BY code ORDER BY trade_date ROWS BETWEEN 4  PRECEDING AND CURRENT ROW),
        w10 AS (PARTITION BY code ORDER BY trade_date ROWS BETWEEN 9  PRECEDING AND CURRENT ROW),
        w20 AS (PARTITION BY code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
        w30 AS (PARTITION BY code ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW),
        w60 AS (PARTITION BY code ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
),
latest AS (
    SELECT * FROM windowed WHERE rn = 1 AND ma60 IS NOT NULL
),
scored AS (
    SELECT
        l.code,
        b.name,
        b.industry,
        l.trade_date::VARCHAR                                                         AS last_date,
        l.close,
        ROUND((l.close - l.close_20d_ago) / NULLIF(l.close_20d_ago, 0) * 100, 2)    AS momentum_20d,
        ROUND((l.close - l.close_60d_ago) / NULLIF(l.close_60d_ago, 0) * 100, 2)    AS momentum_60d,
        (CASE WHEN l.ma5  > l.ma10 THEN 1 ELSE 0 END
       + CASE WHEN l.ma10 > l.ma20 THEN 1 ELSE 0 END
       + CASE WHEN l.ma20 > l.ma60 THEN 1 ELSE 0 END)                                AS ma_score,
        ROUND(CASE WHEN l.vol_ma30 > 0 THEN l.vol_ma10 / l.vol_ma30 ELSE 1.0 END, 2) AS vol_expansion,
        CASE WHEN l.close > l.ma20 THEN 1 ELSE 0 END                                 AS above_ma20
    FROM latest l
    JOIN stock_basic b ON l.code = b.code
    WHERE b.industry IS NOT NULL
)
SELECT
    code, name, industry, last_date, close,
    momentum_20d, momentum_60d, ma_score, vol_expansion, above_ma20,
    ROUND(
        COALESCE(momentum_20d, 0) * 0.3
      + COALESCE(momentum_60d, 0) * 0.2
      + ma_score        * 8.0
      + (vol_expansion - 1.0) * 15.0
      + above_ma20      * 5.0
    , 2) AS trend_score
FROM scored
ORDER BY trend_score DESC
"""

_SECTOR_SQL = """
WITH scored AS ({score_sql}),
ranked AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY industry ORDER BY trend_score DESC) AS rank_in_sector
    FROM ({score_sql}) t
)
SELECT
    industry,
    COUNT(*)                                            AS stock_count,
    ROUND(AVG(trend_score), 2)                          AS avg_score,
    ROUND(AVG(momentum_20d), 2)                         AS avg_momentum_20d,
    ROUND(AVG(momentum_60d), 2)                         AS avg_momentum_60d,
    ROUND(AVG(CAST(ma_score AS DOUBLE)), 2)              AS avg_ma_score,
    MAX(CASE WHEN rank_in_sector = 1 THEN name  END)    AS leader_name,
    MAX(CASE WHEN rank_in_sector = 1 THEN trend_score END) AS leader_score
FROM ranked
GROUP BY industry
HAVING COUNT(*) >= 3
ORDER BY avg_score DESC
"""


def _score_from_db(top_n: int, top_sectors: int):
    """从本地 stock_daily 直接用 SQL 计算趋势评分"""
    conn = get_conn()

    # 检查 stock_daily 数据量（需要 60+ 天 × 足够股票）
    meta = conn.execute(
        "SELECT COUNT(DISTINCT code), MAX(trade_date) FROM stock_daily"
    ).fetchone()
    code_count, max_date = meta
    if code_count < 50:
        return None, None, "stock_daily 数据不足（< 50 只股票），请先同步日线数据"

    # 个股评分
    rows = conn.execute(_SCORE_SQL).fetchall()
    cols = ["code", "name", "industry", "last_date", "close",
            "momentum_20d", "momentum_60d", "ma_score", "vol_expansion", "above_ma20", "trend_score"]
    stocks = [dict(zip(cols, r)) for r in rows[:top_n]]

    # 行业聚合
    sector_sql = _SECTOR_SQL.replace("{score_sql}", _SCORE_SQL)
    sector_rows = conn.execute(sector_sql).fetchall()
    sector_cols = ["industry", "stock_count", "avg_score",
                   "avg_momentum_20d", "avg_momentum_60d", "avg_ma_score",
                   "leader_name", "leader_score"]
    sectors = [dict(zip(sector_cols, r)) for r in sector_rows[:top_sectors]]

    info = f"数据来源：本地 stock_daily（{code_count} 只股票，最新 {max_date}）"
    return stocks, sectors, info


# ─── Provider 路径（本地无数据时的兜底）────────────────────────────────────
# 使用 xshare.utils.safe_call 提供非阻塞 shutdown 的超时保护。


def _score_stock_from_provider(provider, code: str, name: str) -> dict | None:
    """拉取单只股票 60 日历史并打分"""
    try:
        hist = safe_call(provider.get_daily_history, code, None, None, 65)
        if hist is None or len(hist) < 60:
            return None
        hist = hist.sort_values("trade_date").reset_index(drop=True)
        ind = calculate_indicators(hist, ["TREND", "VOL_MA", "MA"])
        trend = ind.get("trend", {})
        vol_ma = ind.get("vol_ma", {})
        ma = ind.get("ma", {})

        close = float(hist["close"].iloc[-1])
        close_20d = float(hist["close"].iloc[-21]) if len(hist) > 20 else close
        close_60d = float(hist["close"].iloc[-61]) if len(hist) > 60 else float(hist["close"].iloc[0])

        momentum_20d = round((close - close_20d) / close_20d * 100, 2) if close_20d else 0
        momentum_60d = round((close - close_60d) / close_60d * 100, 2) if close_60d else 0

        # MA score
        m5 = ma.get("ma5") or 0
        m10 = ma.get("ma10") or 0
        m20 = ma.get("ma20") or 0
        m60 = ma.get("ma60") or 0
        ma_score = (1 if m5 > m10 else 0) + (1 if m10 > m20 else 0) + (1 if m20 > m60 > 0 else 0)

        vol_ratio = float(vol_ma.get("vol_ratio") or 1.0)
        above_ma20 = 1 if close > m20 > 0 else 0
        phase = str(trend.get("phase", ""))

        trend_score = round(
            momentum_20d * 0.3
            + momentum_60d * 0.2
            + ma_score * 8.0
            + (vol_ratio - 1.0) * 15.0
            + above_ma20 * 5.0,
            2,
        )

        return {
            "code": code,
            "name": name,
            "momentum_20d": momentum_20d,
            "momentum_60d": momentum_60d,
            "ma_score": ma_score,
            "vol_expansion": vol_ratio,
            "above_ma20": above_ma20,
            "trend_phase": phase,
            "trend_score": trend_score,
        }
    except Exception as e:
        logger.debug("provider score failed for %s: %s", code, e)
        return None


def _score_from_provider(top_n: int, top_sectors: int, sample_size: int = 100):
    """从 provider 拉取今日涨幅榜 + 股票列表做趋势评分（兜底路径）"""
    provider = get_provider()

    # 种子：今日涨幅榜
    gainers, _ = safe_call(provider.get_top_movers, min(sample_size, 200))
    seed_codes = [(m.code, m.name) for m in gainers]

    # 尝试从本地 stock_basic 补充种子（优先级：今日涨幅 > 随机采样）
    try:
        conn = get_conn()
        extra = conn.execute(
            "SELECT code, name FROM stock_basic ORDER BY RANDOM() LIMIT ?",
            [max(0, sample_size - len(seed_codes))]
        ).fetchall()
        seen = {c for c, _ in seed_codes}
        for code, name in extra:
            if code not in seen:
                seed_codes.append((code, name))
    except Exception:
        pass

    # 并行评分：每个 worker 限时 30s，且用非阻塞 shutdown，
    # 任一 worker 卡在网络/DB 都不会无限挂起整条链。
    results = []
    pool = ThreadPoolExecutor(max_workers=8)
    try:
        futures = {
            pool.submit(_score_stock_from_provider, provider, code, name): (code, name)
            for code, name in seed_codes[:sample_size]
        }
        for future, (code, name) in futures.items():
            try:
                res = future.result(timeout=30)
            except TimeoutError:
                logger.warning("provider 评分超时，跳过 %s %s", code, name)
                continue
            except Exception as e:
                logger.debug("provider 评分失败 %s: %s", code, e)
                continue
            if res:
                results.append(res)
    finally:
        # 关键：cancel_futures + wait=False，避免被卡死的 worker 线程
        # 在退出时阻塞（Python 无法强杀线程，但不再等待它）。
        pool.shutdown(wait=False, cancel_futures=True)

    if not results:
        return None, None, "无法从 provider 获取足够数据"

    results.sort(key=lambda x: x["trend_score"], reverse=True)
    stocks = results[:top_n]

    # 行业聚合（需要 stock_basic 行业信息）
    sectors: dict[str, dict] = {}
    try:
        conn = get_conn()
        code_to_industry = {
            r[0]: r[1]
            for r in conn.execute("SELECT code, industry FROM stock_basic").fetchall()
        }
        for s in results:
            industry = code_to_industry.get(s["code"])
            if not industry:
                continue
            bucket = sectors.setdefault(industry, {
                "industry": industry, "count": 0, "sum_score": 0.0,
                "sum_m20": 0.0, "sum_m60": 0.0, "leader": s["name"], "leader_score": s["trend_score"],
            })
            bucket["count"] += 1
            bucket["sum_score"] += s["trend_score"]
            bucket["sum_m20"] += s["momentum_20d"]
            bucket["sum_m60"] += s["momentum_60d"]
            if s["trend_score"] > bucket["leader_score"]:
                bucket["leader"] = s["name"]
                bucket["leader_score"] = s["trend_score"]
    except Exception:
        pass

    sector_list = [
        {
            "industry": v["industry"],
            "stock_count": v["count"],
            "avg_score": round(v["sum_score"] / v["count"], 2),
            "avg_momentum_20d": round(v["sum_m20"] / v["count"], 2),
            "avg_momentum_60d": round(v["sum_m60"] / v["count"], 2),
            "leader_name": v["leader"],
        }
        for v in sectors.values()
        if v["count"] >= 2
    ]
    sector_list.sort(key=lambda x: x["avg_score"], reverse=True)

    info = f"数据来源：provider（采样 {len(results)} 只股票，含今日涨幅榜）"
    return stocks, sector_list[:top_sectors], info


# ─── 主入口 ────────────────────────────────────────────────────────────────


def _trend_scanner_blocking(args: dict) -> str:
    """同步阻塞核心：DB 评分优先，provider 兜底。跑在 to_thread 中，
    使上层 asyncio.wait_for 超时能真正触发而不卡住事件循环。"""
    top_n = int(args.get("top_n", 30))
    top_sectors = int(args.get("top_sectors", 10))
    force_provider = bool(args.get("force_provider", False))

    stocks, sectors, source_info = None, None, ""

    # 优先走 DB 路径
    if not force_provider:
        stocks, sectors, source_info = _score_from_db(top_n, top_sectors)

    # 兜底：provider 路径
    if stocks is None:
        stocks, sectors, source_info = _score_from_provider(top_n, top_sectors)

    if stocks is None:
        return json.dumps(
            {"error": "无法获取数据，请确认数据源配置", "retry_same_args": False},
            ensure_ascii=False,
        )

    def _tag(score: float) -> str:
        if score >= 30:
            return "强趋势"
        if score >= 15:
            return "趋势中"
        if score >= 5:
            return "弱趋势"
        return "震荡"

    for s in stocks:
        s["trend_tag"] = _tag(float(s.get("trend_score") or 0))

    result = {
        "source": source_info,
        "methodology": (
            "评分 = 20日动量×0.3 + 60日动量×0.2 + 均线多头排列(0-3)×8 "
            "+ 量能放大(vol_ma10/vol_ma30-1)×15 + 收盘>MA20×5"
        ),
        "trending_stocks": stocks,
        "trending_sectors": sectors,
    }

    return json.dumps(result, ensure_ascii=False, default=str)


async def trend_scanner(args: dict) -> str:
    """扫描正在走趋势的股票和行业"""
    # 阻塞核心丢到工作线程，保证事件循环不被占用、上层超时可触发
    return await asyncio.to_thread(_trend_scanner_blocking, args)
