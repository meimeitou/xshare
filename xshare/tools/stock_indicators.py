"""技术指标计算"""

import json

import pandas as pd

from xshare.data.db import get_conn
from xshare.data.provider import get_provider
from xshare.indicators.technical import (
    build_chart_payload,
    calculate_indicators,
    json_safe,
    resample_ohlcv,
)


_DEFAULT_INDICATORS = ["MA", "MACD", "RSI", "KDJ", "BOLL", "TREND"]


def _get_exchange_traded_history(code: str, limit: int = 250) -> pd.DataFrame | None:
    """Return local ETF/index history, or None when code is a stock."""
    conn = get_conn()
    asset = conn.execute(
        """SELECT asset_type FROM (
               SELECT code, 'etf' asset_type FROM etf_basic
               UNION ALL
               SELECT code, 'index' asset_type FROM index_basic
           ) WHERE code = ? LIMIT 1""",
        [code],
    ).fetchone()
    if not asset:
        return None

    table = "fund_daily" if asset[0] == "etf" else "index_daily"
    df = conn.execute(
        f"""SELECT * FROM (
                SELECT * FROM {table} WHERE code = ?
                ORDER BY trade_date DESC LIMIT ?
            ) ORDER BY trade_date""",
        [code, limit],
    ).fetchdf()
    if not df.empty:
        latest = pd.to_datetime(df["trade_date"], errors="coerce").max()
        df.attrs["source"] = "cache"
        df.attrs["as_of"] = latest.strftime("%Y%m%d") if not pd.isna(latest) else None
        df.attrs["is_stale"] = False
    return df


def _probe_exchange_traded_history(code: str, limit: int = 250) -> pd.DataFrame | None:
    """兜底探测：etf_basic/index_basic 未收录该 code 时直接查日线表。

    避免 etf_basic 尚未同步导致 ETF 误走 stock_daily 回退分支。
    """
    conn = get_conn()
    for table in ("fund_daily", "index_daily"):
        try:
            df = conn.execute(
                f"""SELECT * FROM (
                        SELECT * FROM {table} WHERE code = ?
                        ORDER BY trade_date DESC LIMIT ?
                    ) ORDER BY trade_date""",
                [code, limit],
            ).fetchdf()
        except Exception:
            continue
        if not df.empty:
            latest = pd.to_datetime(df["trade_date"], errors="coerce").max()
            df.attrs["source"] = "cache"
            df.attrs["as_of"] = latest.strftime("%Y%m%d") if not pd.isna(latest) else None
            df.attrs["is_stale"] = False
            return df
    return None


async def stock_indicators(args: dict) -> str:
    """计算股票技术指标"""
    code = args.get("code", "")
    if not code:
        return json.dumps(
            {"error": "缺少股票代码", "retry_same_args": False, "hint": "请先用 stock_resolve 确认股票代码"},
            ensure_ascii=False,
        )
    indicators = args.get("indicators") or _DEFAULT_INDICATORS
    period = args.get("period", "daily")

    try:
        df = _get_exchange_traded_history(code)
        if df is None:
            # ETF/指数兜底：etf_basic/index_basic 未收录时直接探测 fund_daily/index_daily
            df = _probe_exchange_traded_history(code)
        if df is None:
            df = get_provider().get_daily_history(code, days=250)
    except Exception as e:
        return json.dumps(
            {"error": f"{code} 数据源暂时不可用：{e}", "retry_same_args": False,
             "hint": "数据源故障，请基于已有数据给出分析结论，不要重试此工具"},
            ensure_ascii=False,
        )

    if df.empty:
        return json.dumps(
            {"error": f"未找到 {code} 的行情数据", "retry_same_args": False,
             "hint": "缓存和数据源均无数据，请基于已有数据给出分析结论"},
            ensure_ascii=False,
        )

    chart_df = resample_ohlcv(df, period)
    result = calculate_indicators(chart_df, indicators)
    result.update(build_chart_payload(chart_df, indicators))
    result["code"] = code
    result["period"] = period
    result["source"] = df.attrs.get("source", "cache")
    result["as_of"] = df.attrs.get("as_of")
    result["is_stale"] = bool(df.attrs.get("is_stale", False))
    if df.attrs.get("coverage_gap"):
        result["coverage_gap"] = True
    # allow_nan=False：避免 Python 写出 NaN，导致 FastAPI 二次序列化 500
    return json.dumps(json_safe(result), ensure_ascii=False, default=str, allow_nan=False)
