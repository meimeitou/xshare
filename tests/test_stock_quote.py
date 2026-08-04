import json

import pytest

from xshare.tools import stock_quote
from xshare.data.provider import RealtimeQuote


@pytest.mark.asyncio
async def test_stock_quote_basic(monkeypatch, fake_provider):
    monkeypatch.setattr(stock_quote, "get_provider", lambda: fake_provider)

    resp = await stock_quote.stock_quote({"code": "002594.SZ"})
    data = json.loads(resp)

    assert data["code"] == "002594.SZ"
    assert data["name"] == "比亚迪"
    assert data["price"] == 250.0
    assert data["current"] == 250.0
    assert data["change_pct"] == 1.5
    assert data["change"] == data["change_amount"]
    assert data["source"] == "fake"


@pytest.mark.asyncio
async def test_stock_quote_serializes_none_fields(monkeypatch):
    """to_dict 应过滤 None 字段。"""

    class PartialProvider:
        def get_realtime_quote(self, code):
            return RealtimeQuote(code=code, name="测试", price=10.0)

    monkeypatch.setattr(stock_quote, "get_provider", lambda: PartialProvider())

    resp = await stock_quote.stock_quote({"code": "000001.SZ"})
    data = json.loads(resp)

    assert data["price"] == 10.0
    assert "pe" not in data  # None 被过滤
