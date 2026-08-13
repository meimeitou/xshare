"""quote_snapshot 缓存 + quote 同步任务 + ProviderManager 缓存优先的验证。"""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from xshare.data import quote_cache
from xshare.data.provider import ProviderManager
from xshare.data.sync_config import (
    ALL_JOBS,
    QUOTE_JOB,
    TRADING_HOURS_JOBS,
    _BLOCKING_HANDLERS,
    _in_trading_hours,
    init_sync_config,
)

TS = datetime(2026, 8, 11, 10, 0, 0)


def _spot_df():
    return pd.DataFrame([
        {"code": "600519.SH", "name": "贵州茅台", "price": 1700.0, "change_pct": 9.9,
         "change_amount": 153.0, "open": 1680.0, "high": 1710.0, "low": 1675.0,
         "prev_close": 1547.0, "volume": 12345.0, "amount": 2.1e9},
        {"code": "000001.SZ", "name": "平安银行", "price": 11.0, "change_pct": -0.5,
         "change_amount": -0.06, "open": 11.1, "high": 11.2, "low": 10.9,
         "prev_close": 11.06, "volume": 9e6, "amount": 1e8},
    ])


def _seed(db_conn, ts=TS):
    idx = pd.DataFrame([
        {"code": "000001.SH", "name": "上证指数", "price": 3200.0, "change_pct": 0.5},
    ])
    sec = pd.DataFrame([
        {"name": "白酒", "change_pct": 2.1, "leader": "贵州茅台", "leader_pct": 9.9},
        {"name": "房地产", "change_pct": -1.5, "leader": "万科A", "leader_pct": -2.0},
    ])
    return quote_cache.write_snapshots(_spot_df(), idx, sec, ts)


# ─── 缓存读写 ────────────────────────────────────────────────

def test_write_and_latest(db_conn):
    assert _seed(db_conn) == 5
    spot = quote_cache.latest_spot()
    assert len(spot) == 2
    q = quote_cache.latest_quote("600519.SH")
    assert q["price"] == 1700.0 and q["ts"] == TS
    assert quote_cache.latest_quote("999999.XX") is None
    assert quote_cache.latest_indices()[0]["name"] == "上证指数"
    assert len(quote_cache.latest_sectors()) == 2


def test_latest_batch_wins(db_conn):
    _seed(db_conn)
    newer = _spot_df()
    newer.loc[0, "price"] = 1701.0
    quote_cache.write_snapshots(newer, None, None, TS + timedelta(minutes=5))
    assert quote_cache.latest_quote("600519.SH")["price"] == 1701.0
    # 指数仍是旧批次
    assert len(quote_cache.latest_indices()) == 1


def test_purge(db_conn):
    _seed(db_conn, ts=TS - timedelta(days=10))
    assert quote_cache.purge(retain_days=5) == 5
    assert quote_cache.latest_spot().empty


# ─── ProviderManager 缓存优先 ────────────────────────────────

@pytest.fixture
def pm_no_akshare(monkeypatch):
    """缓存优先验证：akshare 实时路径一旦被调用即炸。"""
    pm = ProviderManager()
    def _boom(method, *args, **kwargs):
        raise AssertionError(f"不应调用 akshare 实时路径: {method}")
    monkeypatch.setattr(pm, "_call_realtime_akshare_only", _boom)
    return pm


def test_provider_cache_first(db_conn, pm_no_akshare):
    _seed(db_conn)
    quote = pm_no_akshare.get_realtime_quote("600519.SH")
    assert quote.source == "quote_cache" and quote.price == 1700.0

    indices = pm_no_akshare.get_main_indices()
    assert indices[0].name == "上证指数" and indices[0].code == "000001"

    stats = pm_no_akshare.get_market_stats()
    assert (stats.total, stats.up, stats.down, stats.limit_up) == (2, 1, 1, 1)

    assert pm_no_akshare.get_total_turnover() == round((2.1e9 + 1e8) / 1e8, 2)

    gainers, losers = pm_no_akshare.get_top_movers(1)
    assert gainers[0].code == "600519.SH" and losers[0].code == "000001.SZ"

    up, down = pm_no_akshare.get_sector_rankings(1)
    assert up[0].name == "白酒" and up[0].leader == "贵州茅台"
    assert down[0].name == "房地产"


def test_provider_fallback_on_empty_cache(db_conn, pm_no_akshare):
    """缓存为空时应回退 akshare 路径（此处炸出 AssertionError 即证明走了回退）。"""
    with pytest.raises(AssertionError):
        pm_no_akshare.get_top_movers(1)


# ─── quote 任务注册与门禁 ────────────────────────────────────

def test_quote_job_registered(sqlite_conn):
    assert QUOTE_JOB in ALL_JOBS
    assert QUOTE_JOB in TRADING_HOURS_JOBS
    assert QUOTE_JOB in _BLOCKING_HANDLERS
    init_sync_config()
    row = sqlite_conn.execute(
        "SELECT interval_minutes, enabled FROM sync_config WHERE job = ?", [QUOTE_JOB]
    ).fetchone()
    assert row == (5, 1)


def test_trading_hours_gate(db_conn, monkeypatch):
    # trade_cal 为空 → weekday 回退：2026-08-10 周一，2026-08-08 周六
    assert _in_trading_hours(datetime(2026, 8, 10, 10, 0)) is True
    assert _in_trading_hours(datetime(2026, 8, 10, 15, 5)) is True
    assert _in_trading_hours(datetime(2026, 8, 10, 12, 0)) is False
    assert _in_trading_hours(datetime(2026, 8, 10, 9, 0)) is False
    assert _in_trading_hours(datetime(2026, 8, 8, 10, 0)) is False


@pytest.mark.asyncio
async def test_run_job_quote_no_tushare_token(sqlite_conn, monkeypatch):
    """quote 不需要 TUSHARE_TOKEN；handler 走 akshare，这里 stub 掉。"""
    from xshare.data.task_queue import run_job

    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setitem(_BLOCKING_HANDLERS, QUOTE_JOB, lambda payload: 42)
    result = await run_job(QUOTE_JOB)
    assert result["status"] == "ok" and result["synced"] == 42


# ─── source runner：三路独立容错 ─────────────────────────────

def test_sync_quote_snapshot_to_db(db_conn, monkeypatch):
    import xshare.data.sources.akshare_provider as akp
    from xshare.data.sources.quote_snapshot import sync_quote_snapshot_to_db

    monkeypatch.setattr(akp, "_fetch_spot_sina", lambda: _spot_df())
    monkeypatch.setattr(akp, "_fetch_index_spot_sina", lambda: pd.DataFrame())
    def _bad_sector():
        raise RuntimeError("sector down")
    monkeypatch.setattr(akp, "_fetch_sector_spot_sina", _bad_sector)

    n = sync_quote_snapshot_to_db()
    assert n == 2  # 仅个股入库
    assert quote_cache.latest_quote("600519.SH")["price"] == 1700.0
