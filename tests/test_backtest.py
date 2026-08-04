import json
from datetime import date

import pandas as pd
import pytest

from xshare.tools import backtest


def _seed_daily(conn, code="002594.SZ", n=30, start_price=100.0):
    """插入 n 日日线数据，价格单调上涨。"""
    rows = []
    d = date(2026, 1, 1)
    for i in range(n):
        close = round(start_price + i, 2)
        trade_date = d.toordinal() + i
        rows.append((code, date.fromordinal(trade_date), close, close + 0.5, close - 0.5, close, 10000, close * 10000))
    conn.executemany(
        "INSERT INTO stock_daily (code, trade_date, open, high, low, close, volume, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


@pytest.mark.asyncio
async def test_backtest_buy_and_hold(db_conn):
    _seed_daily(db_conn, n=30, start_price=100.0)

    resp = await backtest.backtest_run({
        "strategy": {"name": "ma_cross"},
        "target": "002594.SZ",
        "start_date": "20260101",
        "end_date": "20260130",
    })
    data = json.loads(resp)

    assert data["strategy"] == "ma_cross"
    assert data["target"] == "002594.SZ"
    assert data["trade_days"] == 30
    # 价格从 100 涨到 129 → 总收益约 29%
    assert data["total_return_pct"] > 0
    assert data["annual_return_pct"] > 0
    assert data["max_drawdown_pct"] <= 0  # 单调上涨回撤为 0
    assert "disclaimer" in data
    assert "历史业绩" in data["disclaimer"]


@pytest.mark.asyncio
async def test_backtest_missing_data(db_conn):
    resp = await backtest.backtest_run({
        "strategy": {"name": "rsi"},
        "target": "999999.SZ",
        "start_date": "20260101",
        "end_date": "20260130",
    })
    data = json.loads(resp)

    assert "error" in data
    assert "未找到" in data["error"]


@pytest.mark.asyncio
async def test_backtest_date_normalization(db_conn):
    """start_date/end_date 支持 YYYY-MM-DD 或 YYYYMMDD。"""
    _seed_daily(db_conn, n=10)

    resp = await backtest.backtest_run({
        "strategy": {},
        "target": "002594.SZ",
        "start_date": "2026-01-01",
        "end_date": "2026-01-10",
    })
    data = json.loads(resp)

    assert data["trade_days"] == 10


@pytest.mark.asyncio
async def test_backtest_default_strategy_name(db_conn):
    _seed_daily(db_conn, n=5)

    resp = await backtest.backtest_run({
        "strategy": {},
        "target": "002594.SZ",
        "start_date": "20260101",
        "end_date": "20260105",
    })
    data = json.loads(resp)

    assert data["strategy"] == "buy_and_hold"
