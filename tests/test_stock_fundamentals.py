import json
import math

import pandas as pd
import pytest

from xshare.tools import stock_fundamentals
from xshare.data.provider import DataFetchError


@pytest.mark.asyncio
async def test_stock_fundamentals_basic(monkeypatch, fake_provider):
    monkeypatch.setattr(stock_fundamentals, "get_provider", lambda: fake_provider)

    resp = await stock_fundamentals.stock_fundamentals({"code": "002594.SZ"})
    data = json.loads(resp)

    assert data["code"] == "002594.SZ"
    assert "pe" in data
    assert "roe" in data
    assert "revenue" in data
    assert "net_profit" in data
    # 派生指标
    assert data["peg"] is not None
    assert data["pe_percentile"] is not None
    assert len(data["roe_trend"]) > 0
    assert data["revenue_growth"] is not None
    assert "net_margin_trend" in data
    assert len(data["revenue_trend"]) > 0
    assert len(data["profit_trend"]) > 0


@pytest.mark.asyncio
async def test_stock_fundamentals_empty_df(monkeypatch):
    class EmptyProvider:
        def get_financial_data(self, code):
            return pd.DataFrame()

    monkeypatch.setattr(stock_fundamentals, "get_provider", lambda: EmptyProvider())

    resp = await stock_fundamentals.stock_fundamentals({"code": "002594.SZ"})
    data = json.loads(resp)

    assert "error" in data
    assert "未找到" in data["error"]


@pytest.mark.asyncio
async def test_stock_fundamentals_data_fetch_error(monkeypatch):
    class ErrProvider:
        def get_financial_data(self, code):
            raise DataFetchError("all sources failed")

    monkeypatch.setattr(stock_fundamentals, "get_provider", lambda: ErrProvider())

    resp = await stock_fundamentals.stock_fundamentals({"code": "002594.SZ"})
    data = json.loads(resp)

    assert "error" in data
    assert "all sources failed" in data["error"]


@pytest.mark.asyncio
async def test_stock_fundamentals_nan_is_json_safe(monkeypatch):
    class NanProvider:
        def get_financial_data(self, code):
            return pd.DataFrame(
                [
                    {
                        "code": code,
                        "end_date": pd.Timestamp("2026-03-31"),
                        "pe": float("nan"),
                        "pb": 3.2,
                        "roe": 12.1,
                        "revenue": 1000.0,
                        "net_profit": 120.0,
                        "revenue_yoy": 10.0,
                        "profit_yoy": float("inf"),
                    }
                ]
            )

    monkeypatch.setattr(stock_fundamentals, "get_provider", lambda: NanProvider())

    resp = await stock_fundamentals.stock_fundamentals({"code": "002594.SZ"})
    data = json.loads(resp)

    assert data["pe"] is None
    assert data["profit_yoy"] is None
    assert isinstance(data["end_date"], str)
    assert not any(isinstance(v, float) and (math.isnan(v) or math.isinf(v)) for v in data.values())
