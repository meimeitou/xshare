import json

import pandas as pd
import pytest

from xshare.tools import stock_indicators
from xshare.data.provider import DataFetchError
from tests.conftest import make_daily_history


@pytest.mark.asyncio
async def test_stock_indicators_basic(monkeypatch, fake_provider):
    monkeypatch.setattr(stock_indicators, "get_provider", lambda: fake_provider)

    resp = await stock_indicators.stock_indicators({
        "code": "002594.SZ",
        "indicators": ["MA", "MACD", "RSI", "TREND"],
    })
    data = json.loads(resp)

    assert data["code"] == "002594.SZ"
    assert data["period"] == "daily"
    assert "ma" in data
    assert data["ma"]["ma5"] is not None
    assert "macd" in data
    assert "dif" in data["macd"]
    assert "rsi" in data
    assert "trend" in data
    assert data["trend"]["phase"]  # 非空阶段
    assert "bars" in data and len(data["bars"]) > 0
    assert "MA" in data and len(data["MA"]) == 4
    assert "MACD" in data and len(data["MACD"]) > 0


@pytest.mark.asyncio
async def test_stock_indicators_default_indicators(monkeypatch, fake_provider):
    """indicators 缺省时使用默认列表。"""
    monkeypatch.setattr(stock_indicators, "get_provider", lambda: fake_provider)

    resp = await stock_indicators.stock_indicators({"code": "002594.SZ"})
    data = json.loads(resp)

    assert data["code"] == "002594.SZ"
    assert "ma" in data
    assert "macd" in data
    assert "trend" in data


@pytest.mark.asyncio
async def test_stock_indicators_missing_code(monkeypatch, fake_provider):
    monkeypatch.setattr(stock_indicators, "get_provider", lambda: fake_provider)

    resp = await stock_indicators.stock_indicators({"code": ""})
    data = json.loads(resp)

    assert "error" in data
    assert data["retry_same_args"] is False


@pytest.mark.asyncio
async def test_stock_indicators_provider_error(monkeypatch):
    class ErrProvider:
        def get_daily_history(self, code, start_date=None, end_date=None, days=120):
            raise ConnectionError("boom")

    monkeypatch.setattr(stock_indicators, "get_provider", lambda: ErrProvider())

    resp = await stock_indicators.stock_indicators({
        "code": "002594.SZ",
        "indicators": ["MA"],
    })
    data = json.loads(resp)

    assert "error" in data
    assert "数据源暂时不可用" in data["error"]
    assert data["retry_same_args"] is False


@pytest.mark.asyncio
async def test_stock_indicators_monthly_no_nan(monkeypatch, fake_provider):
    """月线样本少时 RSI 可能为 NaN，响应必须是严格 JSON（无 NaN）。"""
    monkeypatch.setattr(stock_indicators, "get_provider", lambda: fake_provider)

    resp = await stock_indicators.stock_indicators({
        "code": "002594.SZ",
        "indicators": ["MA", "MACD", "RSI", "KDJ", "BOLL"],
        "period": "monthly",
    })
    assert "NaN" not in resp
    data = json.loads(resp)
    assert data["period"] == "monthly"
    assert "bars" in data
    # rsi 摘要可为 null，但不能是 nan
    if isinstance(data.get("rsi"), dict) and "rsi" in data["rsi"]:
        assert data["rsi"]["rsi"] is None or isinstance(data["rsi"]["rsi"], (int, float))


@pytest.mark.asyncio
async def test_stock_indicators_empty_df(monkeypatch):
    class EmptyProvider:
        def get_daily_history(self, code, start_date=None, end_date=None, days=120):
            return pd.DataFrame()

    monkeypatch.setattr(stock_indicators, "get_provider", lambda: EmptyProvider())

    resp = await stock_indicators.stock_indicators({"code": "002594.SZ", "indicators": ["MA"]})
    data = json.loads(resp)

    assert "error" in data
    assert "未找到" in data["error"]


@pytest.mark.asyncio
async def test_stock_indicators_reads_etf_from_fund_daily(db_conn, monkeypatch):
    db_conn.execute(
        "INSERT INTO etf_basic(code, name, exchange) VALUES ('159005.SZ', '添富快线ETF', 'SZ')"
    )
    history = make_daily_history(code="159005.SZ", n=80)
    db_conn.register("etf_history", history)
    db_conn.execute(
        """INSERT INTO fund_daily(code, trade_date, open, high, low, close, volume, amount)
           SELECT code, trade_date, open, high, low, close, volume, amount FROM etf_history"""
    )

    class UnexpectedProvider:
        def get_daily_history(self, *args, **kwargs):
            raise AssertionError("ETF history must not read stock_daily/provider")

    monkeypatch.setattr(stock_indicators, "get_provider", lambda: UnexpectedProvider())
    resp = await stock_indicators.stock_indicators(
        {"code": "159005.SZ", "indicators": ["MA", "MACD"]}
    )
    data = json.loads(resp)

    assert "error" not in data
    assert data["code"] == "159005.SZ"
    assert len(data["bars"]) == 80
    assert data["source"] == "cache"
