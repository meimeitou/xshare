"""实时路径：仅 AkShare；个股失败回退本地缓存，不走 Tushare。"""

from datetime import date

import pytest

from xshare.data.provider import (
    DataFetchError,
    IndexQuote,
    ProviderManager,
    RealtimeQuote,
)


class _AkShareStub:
    name = "akshare"
    priority = 1

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def get_realtime_quote(self, code: str) -> RealtimeQuote:
        self.calls += 1
        if self.fail:
            raise RuntimeError("akshare down")
        return RealtimeQuote(code=code, price=10.0, source="akshare")

    def get_main_indices(self) -> list[IndexQuote]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("akshare down")
        return [IndexQuote(code="000001.SH", name="上证指数", price=3200.0, change_pct=0.5)]


class _TushareStub:
    name = "tushare"
    priority = 0  # 优先级更高，但实时路径不得调用

    def __init__(self):
        self.calls = 0

    def get_realtime_quote(self, code: str) -> RealtimeQuote:
        self.calls += 1
        return RealtimeQuote(code=code, price=9.5, source="tushare", is_delayed=True)

    def get_main_indices(self) -> list[IndexQuote]:
        self.calls += 1
        return [IndexQuote(code="000001.SH", name="上证指数", price=3190.0, change_pct=-0.1)]


def test_realtime_quote_uses_akshare_even_if_tushare_higher_priority():
    mgr = ProviderManager()
    ak = _AkShareStub()
    ts = _TushareStub()
    mgr.register(ts)
    mgr.register(ak)

    quote = mgr.get_realtime_quote("000001.SZ")

    assert quote.source == "akshare"
    assert quote.price == 10.0
    assert quote.is_delayed is False
    assert ak.calls == 1
    assert ts.calls == 0


def test_realtime_quote_skips_tushare_and_uses_cache_when_akshare_fails(db_conn):
    db_conn.execute(
        "INSERT INTO stock_daily (code, trade_date, open, high, low, close, volume, amount) VALUES "
        "('000001.SZ', ?, 10, 11, 9, 10.5, 1000, 10000),"
        "('000001.SZ', ?, 10.5, 11, 10, 11.0, 2000, 20000)",
        [date(2026, 7, 20), date(2026, 7, 21)],
    )

    mgr = ProviderManager()
    ak = _AkShareStub(fail=True)
    ts = _TushareStub()
    mgr.register(ts)
    mgr.register(ak)

    quote = mgr.get_realtime_quote("000001.SZ")

    assert quote.source == "cache"
    assert quote.is_delayed is True
    assert quote.price == 11.0
    assert quote.prev_close == 10.5
    assert ak.calls == 1
    assert ts.calls == 0


def test_realtime_quote_falls_back_to_local_cache(db_conn):
    db_conn.execute(
        "INSERT INTO stock_daily (code, trade_date, open, high, low, close, volume, amount) VALUES "
        "('000001.SZ', ?, 10, 11, 9, 10.5, 1000, 10000),"
        "('000001.SZ', ?, 10.5, 11, 10, 11.0, 2000, 20000)",
        [date(2026, 7, 20), date(2026, 7, 21)],
    )

    mgr = ProviderManager()
    mgr.register(_AkShareStub(fail=True))

    quote = mgr.get_realtime_quote("000001.SZ")

    assert quote.source == "cache"
    assert quote.is_delayed is True
    assert quote.price == 11.0
    assert quote.prev_close == 10.5


def test_market_indices_akshare_only_no_tushare_fallback():
    mgr = ProviderManager()
    ak = _AkShareStub(fail=True)
    ts = _TushareStub()
    mgr.register(ts)
    mgr.register(ak)

    with pytest.raises(DataFetchError):
        mgr.get_main_indices()

    assert ak.calls == 1
    assert ts.calls == 0


def test_realtime_quote_raises_when_all_sources_and_cache_empty(db_conn):
    mgr = ProviderManager()
    mgr.register(_AkShareStub(fail=True))

    with pytest.raises(DataFetchError):
        mgr.get_realtime_quote("999999.SZ")
