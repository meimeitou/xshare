"""实时行情快照缓存（DuckDB）。

quote 同步任务（交易时段每 5 分钟）把 akshare 新浪快照写入
quote_snapshot / index_snapshot / sector_snapshot；ProviderManager
实时读路径优先从这里取数，未命中再回退实时接口。

本模块只做 DuckDB 读写，不 import provider，避免循环依赖。
"""

from datetime import datetime, timedelta

import pandas as pd

from xshare.data.db import get_conn

_QUOTE_COLS = (
    "code", "name", "price", "change_pct", "change_amount",
    "open", "high", "low", "prev_close", "volume", "amount",
    "turnover", "pe", "pb", "total_mv",
)


def _fill_missing(df: pd.DataFrame, cols: tuple[str, ...]) -> pd.DataFrame:
    """补齐缺失列（None），保证 register + INSERT SELECT 不炸。"""
    df = df.copy()
    for col in cols:
        if col not in df.columns:
            df[col] = None
    return df


def write_snapshots(
    spot_df: pd.DataFrame | None,
    index_df: pd.DataFrame | None,
    sector_df: pd.DataFrame | None,
    ts: datetime,
) -> int:
    """写入一批快照，返回总行数。spot/index/sector 可独立为 None（跳过该表）。"""
    conn = get_conn()
    total = 0
    if spot_df is not None and not spot_df.empty:
        df = _fill_missing(spot_df, _QUOTE_COLS)
        df["ts"] = ts
        conn.register("_spot_in", df)
        conn.execute(
            f"INSERT OR REPLACE INTO quote_snapshot ({', '.join(_QUOTE_COLS)}, ts) "
            f"SELECT {', '.join(_QUOTE_COLS)}, ts FROM _spot_in"
        )
        conn.unregister("_spot_in")
        total += len(df)
    if index_df is not None and not index_df.empty:
        df = _fill_missing(index_df, ("code", "name", "price", "change_pct"))
        df["ts"] = ts
        conn.register("_idx_in", df)
        conn.execute(
            "INSERT OR REPLACE INTO index_snapshot (code, name, price, change_pct, ts) "
            "SELECT code, name, price, change_pct, ts FROM _idx_in"
        )
        conn.unregister("_idx_in")
        total += len(df)
    if sector_df is not None and not sector_df.empty:
        df = _fill_missing(sector_df, ("name", "change_pct", "leader", "leader_pct"))
        df["ts"] = ts
        conn.register("_sector_in", df)
        conn.execute(
            "INSERT OR REPLACE INTO sector_snapshot (name, change_pct, leader, leader_pct, ts) "
            "SELECT name, change_pct, leader, leader_pct, ts FROM _sector_in"
        )
        conn.unregister("_sector_in")
        total += len(df)
    return total


def _latest_ts(conn, table: str):
    row = conn.execute(f"SELECT MAX(ts) FROM {table}").fetchone()
    return row[0] if row else None


def latest_spot() -> pd.DataFrame:
    """最新一批全市场个股快照；无数据时返回空 DataFrame。"""
    conn = get_conn()
    ts = _latest_ts(conn, "quote_snapshot")
    if ts is None:
        return pd.DataFrame(columns=list(_QUOTE_COLS) + ["ts"])
    return conn.execute(
        f"SELECT {', '.join(_QUOTE_COLS)}, ts FROM quote_snapshot WHERE ts = ?", [ts]
    ).df()


def latest_quote(code: str) -> dict | None:
    """单只个股最新快照（dict），无则 None。"""
    conn = get_conn()
    row = conn.execute(
        f"SELECT {', '.join(_QUOTE_COLS)}, ts FROM quote_snapshot "
        "WHERE code = ? ORDER BY ts DESC LIMIT 1",
        [code],
    ).fetchone()
    if not row:
        return None
    return dict(zip(list(_QUOTE_COLS) + ["ts"], row))


def latest_indices() -> list[dict]:
    """最新一批指数快照。"""
    conn = get_conn()
    ts = _latest_ts(conn, "index_snapshot")
    if ts is None:
        return []
    rows = conn.execute(
        "SELECT code, name, price, change_pct, ts FROM index_snapshot WHERE ts = ?",
        [ts],
    ).fetchall()
    return [
        dict(zip(("code", "name", "price", "change_pct", "ts"), r)) for r in rows
    ]


def latest_sectors() -> list[dict]:
    """最新一批板块快照。"""
    conn = get_conn()
    ts = _latest_ts(conn, "sector_snapshot")
    if ts is None:
        return []
    rows = conn.execute(
        "SELECT name, change_pct, leader, leader_pct, ts FROM sector_snapshot WHERE ts = ?",
        [ts],
    ).fetchall()
    return [
        dict(zip(("name", "change_pct", "leader", "leader_pct", "ts"), r))
        for r in rows
    ]


def purge(retain_days: int = 5) -> int:
    """删除 retain_days 天前的快照，返回删除总行数。0 表示全部删除。"""
    conn = get_conn()
    cutoff = datetime.now() - timedelta(days=retain_days)
    total = 0
    for table in ("quote_snapshot", "index_snapshot", "sector_snapshot"):
        n = conn.execute(
            f"DELETE FROM {table} WHERE ts < ? RETURNING 1", [cutoff]
        ).fetchall()
        total += len(n)
    return total
