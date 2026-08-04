import json

import pytest

from xshare.tools import market_overview
from tests.conftest import FakeProvider, FailingProvider


@pytest.mark.asyncio
async def test_market_overview_basic(monkeypatch, fake_provider):
    monkeypatch.setattr(market_overview, "get_provider", lambda: fake_provider)

    resp = await market_overview.market_overview({})
    data = json.loads(resp)

    assert "indices" in data
    assert data["indices"]["sh_index"]["name"] == "上证指数"
    assert data["indices"]["sh_index"]["price"] == 3200.0

    assert data["market_stats"]["total"] == 5000
    assert data["market_stats"]["up"] == 3200

    assert data["total_turnover_yi"] == 12345.6

    assert len(data["sector_top_up"]) == 1
    assert data["sector_top_up"][0]["name"] == "AI算力"

    assert data["northbound"]["total"] == 25.6

    assert len(data["top_gainers"]) == 2
    assert data["top_gainers"][0]["code"] == "002594.SZ"
    assert len(data["top_losers"]) == 1


@pytest.mark.asyncio
async def test_market_overview_all_errors(monkeypatch):
    """所有数据源均失败时，应返回 *_error 字段而非抛异常。"""
    monkeypatch.setattr(market_overview, "get_provider", lambda: FailingProvider())

    resp = await market_overview.market_overview({})
    data = json.loads(resp)

    assert "indices_error" in data
    assert "market_stats_error" in data
    assert "turnover_error" in data
    assert "sector_error" in data
    assert "northbound_error" in data
    assert "movers_error" in data


@pytest.mark.asyncio
async def test_market_overview_partial_failure(monkeypatch):
    """部分数据源失败时，成功部分应正常返回。"""

    class PartialProvider(FakeProvider):
        def get_market_stats(self):
            raise RuntimeError("stats down")

        def get_northbound_flow(self):
            raise RuntimeError("north down")

    monkeypatch.setattr(market_overview, "get_provider", lambda: PartialProvider())

    resp = await market_overview.market_overview({})
    data = json.loads(resp)

    assert "indices" in data
    assert "market_stats_error" in data
    assert data["total_turnover_yi"] == 12345.6
    assert "northbound_error" in data
    assert len(data["top_gainers"]) == 2
