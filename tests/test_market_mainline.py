import json
from dataclasses import dataclass

import pandas as pd
import pytest

from xshare.data.provider import IndexQuote, MarketStats, TopMover
from xshare.tools import market_mainline as mm


@dataclass
class FakeSector:
    name: str
    change_pct: float
    leader: str
    leader_pct: float


class FakeProvider:
    def get_main_indices(self):
        return [
            IndexQuote(code="000001.SH", name="上证指数", price=3200.0, change_pct=1.2),
            IndexQuote(code="399001.SZ", name="深证成指", price=10100.0, change_pct=0.8),
            IndexQuote(code="399006.SZ", name="创业板指", price=2100.0, change_pct=1.0),
        ]

    def get_market_stats(self):
        return MarketStats(total=5000, up=3200, down=1500, flat=300, limit_up=80, limit_down=5)

    def get_northbound_flow(self):
        return {"total": 25.6, "sh_connect": 13.2, "sz_connect": 12.4, "date": "20260419"}

    def get_total_turnover(self):
        return 12345.6

    def get_sector_rankings(self, top_n=8):
        top_up = [
            FakeSector("AI算力", 4.2, "中际旭创", 8.1),
            FakeSector("半导体", 3.5, "寒武纪", 10.0),
            FakeSector("机器人", 2.7, "鸣志电器", 6.2),
        ]
        return top_up[:top_n], []

    def get_top_movers(self, top_n=20):
        gainers = [
            TopMover(code="300001.SZ", name="特锐德", price=30.1, change_pct=9.8),
            TopMover(code="300002.SZ", name="神州泰岳", price=11.2, change_pct=8.7),
            TopMover(code="300003.SZ", name="乐普医疗", price=18.6, change_pct=7.9),
        ]
        return gainers[:top_n], []

    def get_daily_history(self, code, start_date=None, end_date=None, days=140):
        n = 120
        close = pd.Series([20 + i * 0.1 for i in range(n)])
        high = close + 0.5
        low = close - 0.5
        volume = pd.Series([100000 + i * 100 for i in range(n)])
        amount = close * volume
        return pd.DataFrame(
            {
                "code": [code] * n,
                "trade_date": pd.date_range("2026-01-01", periods=n).date,
                "open": close,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "amount": amount,
            }
        )


class FallbackSectorProvider(FakeProvider):
    def get_sector_rankings(self, top_n=8):
        raise RuntimeError("sector api unavailable")

    def get_stock_list(self):
        return pd.DataFrame(
            {
                "code": ["300001.SZ", "300002.SZ", "300003.SZ"],
                "name": ["特锐德", "神州泰岳", "乐普医疗"],
                "industry": ["电力设备", "计算机", "医药生物"],
            }
        )


@pytest.mark.asyncio
async def test_market_mainline_basic(monkeypatch):
    monkeypatch.setattr(mm, "get_provider", lambda: FakeProvider())

    resp = await mm.market_mainline({"sector_top_n": 3, "strong_limit": 2})
    data = json.loads(resp)

    assert "error" not in data
    assert data["market_phase"]
    assert data["mainline_direction"]

    assert len(data["mainline_sectors"]) == 3
    assert data["mainline_sectors"][0]["name"] == "AI算力"

    assert len(data["strong_stocks"]) == 2
    first = data["strong_stocks"][0]
    assert "code" in first
    assert "score" in first
    assert "trend_phase" in first
    assert "vol_ratio" in first


@pytest.mark.asyncio
async def test_market_mainline_sector_fallback(monkeypatch):
    monkeypatch.setattr(mm, "get_provider", lambda: FallbackSectorProvider())

    resp = await mm.market_mainline({"sector_top_n": 3, "strong_limit": 2})
    data = json.loads(resp)

    assert "error" not in data
    assert data["sector_data_source"] == "fallback_top_movers"
    assert "sector_error" in data
    assert len(data["mainline_sectors"]) > 0
