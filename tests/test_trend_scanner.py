import json
from datetime import date

import pandas as pd
import pytest

from xshare.tools import trend_scanner
from tests.conftest import make_daily_history


@pytest.mark.asyncio
async def test_trend_scanner_db_insufficient_fallback_to_provider(db_conn, monkeypatch, fake_provider):
    """本地 stock_daily 数据不足（< 50 只）时，应回落到 provider 路径。"""
    monkeypatch.setattr(trend_scanner, "get_provider", lambda: fake_provider)

    resp = await trend_scanner.trend_scanner({"top_n": 5, "top_sectors": 3})
    data = json.loads(resp)

    assert "error" not in data
    assert "provider" in data["source"]
    assert len(data["trending_stocks"]) <= 5
    for s in data["trending_stocks"]:
        assert "trend_score" in s
        assert "trend_tag" in s


@pytest.mark.asyncio
async def test_trend_scanner_force_provider(db_conn, monkeypatch, fake_provider):
    """force_provider=True 时跳过 DB 直接走 provider。"""
    monkeypatch.setattr(trend_scanner, "get_provider", lambda: fake_provider)

    resp = await trend_scanner.trend_scanner({"top_n": 2, "force_provider": True})
    data = json.loads(resp)

    assert "error" not in data
    assert "provider" in data["source"]
    assert len(data["trending_stocks"]) <= 2


@pytest.mark.asyncio
async def test_trend_scanner_trend_tags(monkeypatch, fake_provider):
    monkeypatch.setattr(trend_scanner, "get_provider", lambda: fake_provider)

    resp = await trend_scanner.trend_scanner({"top_n": 10, "force_provider": True})
    data = json.loads(resp)

    valid_tags = {"强趋势", "趋势中", "弱趋势", "震荡"}
    for s in data["trending_stocks"]:
        assert s["trend_tag"] in valid_tags


@pytest.mark.asyncio
async def test_trend_scanner_no_data(monkeypatch):
    """provider 无任何可用数据时返回错误。"""

    class EmptyProvider:
        def get_top_movers(self, top_n=5):
            return [], []

    monkeypatch.setattr(trend_scanner, "get_provider", lambda: EmptyProvider())

    resp = await trend_scanner.trend_scanner({"force_provider": True})
    data = json.loads(resp)

    assert "error" in data


@pytest.mark.asyncio
async def test_trend_scanner_db_path(db_conn, monkeypatch):
    """本地 stock_daily 数据充足时走 SQL 路径。"""
    # 插入 60 只股票 × 70 个交易日
    d0 = date(2026, 1, 1)
    rows = []
    for i in range(60):
        code = f"{600000 + i}.SH"
        for j in range(70):
            close = round(10 + j * 0.2 + i * 0.01, 2)
            rows.append((code, date.fromordinal(d0.toordinal() + j),
                         close, close + 0.1, close - 0.1, close, 10000, close * 10000))
        db_conn.execute(
            "INSERT INTO stock_basic (code, name, industry) VALUES (?, ?, ?)",
            [code, f"股票{i}", "测试行业"],
        )
    db_conn.executemany(
        "INSERT INTO stock_daily (code, trade_date, open, high, low, close, volume, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )

    resp = await trend_scanner.trend_scanner({"top_n": 10, "top_sectors": 5})
    data = json.loads(resp)

    assert "error" not in data
    assert "本地 stock_daily" in data["source"]
    assert len(data["trending_stocks"]) == 10
    assert len(data["trending_sectors"]) <= 5
    for s in data["trending_stocks"]:
        assert "trend_score" in s
