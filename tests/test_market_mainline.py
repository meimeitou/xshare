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
    monkeypatch.setattr(mm, "_read_mainline_cache", lambda: None)
    monkeypatch.setattr(mm, "_score_mainline_from_db", lambda *a, **k: None)

    resp = await mm.market_mainline({"sector_top_n": 3, "strong_limit": 2})
    data = json.loads(resp)

    assert "error" not in data
    assert data["market_phase"]
    assert data["market_snapshot"]["northbound"]["is_stale"] is True
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
    monkeypatch.setattr(mm, "_read_mainline_cache", lambda: None)
    monkeypatch.setattr(mm, "_score_mainline_from_db", lambda *a, **k: None)

    resp = await mm.market_mainline({"sector_top_n": 3, "strong_limit": 2})
    data = json.loads(resp)

    assert "error" not in data
    assert data["sector_data_source"] == "fallback_top_movers"
    assert "sector_error" in data
    assert len(data["mainline_sectors"]) > 0



@pytest.mark.asyncio
async def test_market_mainline_offline_3d_resonance(monkeypatch):
    """离线三维度共振路径：当 DB 有数据时优先返回 offline_3d_resonance 结果。"""
    monkeypatch.setattr(mm, "_read_mainline_cache", lambda: None)
    fake_offline = {
        "market_phase": "情绪高潮（涨停潮+资金流入）",
        "mainline_direction": "光纤概念、CPO概念",
        "mainline_sectors": [
            {"name": "光纤概念", "code": "000123.DC", "pct_change": 3.66,
             "hot": 914.0, "zt_num": 4, "net_amount": 84.13,
             "lead_stock": "中际旭创", "resonance_score": 604.9,
             "strength_tag": "主线"},
        ],
        "limit_ladder": {"5连+": 1, "4连": 1, "3连": 7, "2连": 7, "首板": 76, "total_zt": 92},
        "market_moneyflow": {"net_amount": 2.47e10, "buy_elg_amount": 2.69e10},
        "strong_stocks": [
            {"code": "300308.SZ", "name": "中际旭创", "concept": "光纤概念",
             "limit_times": 0, "net_mf_amount": 33.90, "top_list_net": 0.0,
             "score": 16.8},
        ],
    }
    monkeypatch.setattr(mm, "_score_mainline_from_db", lambda *a, **k: fake_offline)

    resp = await mm.market_mainline({"sector_top_n": 8, "strong_limit": 10})
    data = json.loads(resp)

    assert "error" not in data
    assert data["data_source"] == "offline_3d_resonance"
    assert data["methodology"] == "资金+情绪+逻辑三维共振"
    assert data["market_phase"] == "情绪高潮（涨停潮+资金流入）"
    assert len(data["mainline_sectors"]) == 1
    assert data["mainline_sectors"][0]["name"] == "光纤概念"
    assert data["mainline_sectors"][0]["resonance_score"] == 604.9
    assert data["limit_ladder"]["total_zt"] == 92
    assert len(data["strong_stocks"]) == 1
    assert data["strong_stocks"][0]["code"] == "300308.SZ"


def test_score_mainline_from_db_degradation(monkeypatch):
    """新表为空时 _score_mainline_from_db 应返回 None，触发降级。"""
    from xshare.data.db import get_conn

    # 用临时内存 DB 模拟空表
    import duckdb
    mem = duckdb.connect(":memory:")
    # 复制空表结构
    schema = get_conn().execute("DESCRIBE concept_board").fetchall()
    cols = ", ".join(
        f"{r[0]} {r[1]}" for r in schema
    )
    mem.execute(f"CREATE TABLE concept_board({cols})")
    # stock_daily 也需要空表 → 返回 None
    mem.execute("CREATE TABLE stock_daily(trade_date DATE)")

    monkeypatch.setattr(mm, "get_conn", lambda: mem)
    result = mm._score_mainline_from_db(8, 10)
    assert result is None

@pytest.mark.asyncio
async def test_market_mainline_cache_hit(monkeypatch):
    """mainline_cache 有缓存时优先返回缓存结果，含 cached_at 和 data_date。"""
    fake_cached = {
        "market_phase": "情绪回暖（涨停活跃+资金流入）",
        "mainline_direction": "AI算力、CPO概念",
        "mainline_sectors": [
            {"name": "AI算力", "code": "000001.DC", "change_pct": 4.5,
             "strength_tag": "主线", "resonance_score": 100.0},
        ] * 8,
        "strong_stocks": [
            {"code": "300308.SZ", "name": "中际旭创", "score": 20.0},
        ] * 10,
    }
    monkeypatch.setattr(mm, "_read_mainline_cache", lambda: {
        **fake_cached,
        "data_source": "offline_3d_resonance",
        "methodology": "资金+情绪+逻辑三维共振",
        "cached_at": "2026-08-14 17:30:00",
        "data_date": "2026-08-14",
    })
    # _score_mainline_from_db 不应被调用
    called = []
    monkeypatch.setattr(mm, "_score_mainline_from_db", lambda *a, **k: called.append(1) or {"unexpected": True})

    resp = await mm.market_mainline({"sector_top_n": 8, "strong_limit": 10})
    data = json.loads(resp)

    assert called == [], "缓存命中时不应调用 _score_mainline_from_db"
    assert data["data_source"] == "offline_3d_resonance"
    assert data["data_date"] == "2026-08-14"
    assert data["cached_at"] == "2026-08-14 17:30:00"
    assert data["market_phase"] == "情绪回暖（涨停活跃+资金流入）"
    assert len(data["mainline_sectors"]) == 8
    assert data["mainline_sectors"][0]["name"] == "AI算力"
