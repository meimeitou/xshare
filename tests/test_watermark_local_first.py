"""水位、限速与 local-first Provider 回归测试。"""

from datetime import date, timedelta

import pandas as pd
import pytest

from xshare.data import rate_limit, watermark as wm
from xshare.data.provider import DataFetchError, ProviderManager
from xshare.data.sources import tushare_source


def test_watermark_set_get_and_gaps(db_conn):
    d1 = date(2026, 7, 1)
    d2 = date(2026, 7, 2)
    d3 = date(2026, 7, 3)
    wm.set_watermark(wm.DATASET_DAILY, d1, wm.STATUS_OK, 100)
    wm.set_watermark(wm.DATASET_DAILY, d3, wm.STATUS_OK, 100)

    assert wm.get_watermark(wm.DATASET_DAILY, d1)["status"] == "ok"
    assert wm.latest_ok_key(wm.DATASET_DAILY) == "2026-07-03"
    gaps = wm.find_daily_gaps([d1, d2, d3])
    assert gaps == [d2]

    summary = wm.summarize(wm.DATASET_DAILY)
    assert summary["ok_count"] == 2
    assert summary["latest_ok_key"] == "2026-07-03"


def test_rate_limiter_waits(monkeypatch):
    rate_limit.reset_for_tests()
    lim = rate_limit.RateLimiter(name="test", min_interval=0.05)
    sleeps: list[float] = []

    def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(rate_limit.time, "sleep", fake_sleep)
    lim.acquire()
    lim.acquire()
    assert lim.call_count == 2
    assert lim.wait_count >= 1
    assert sleeps and sleeps[0] > 0


def test_is_rate_limit_error():
    assert rate_limit.is_rate_limit_error(
        Exception("抱歉，您访问接口(daily)频率超限(500次/分钟)")
    )
    assert not rate_limit.is_rate_limit_error(Exception("token invalid"))


def test_is_transient_error():
    assert rate_limit.is_transient_error(ConnectionResetError(54, "Connection reset by peer"))
    assert rate_limit.is_transient_error(
        Exception("('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer'))")
    )
    assert rate_limit.is_transient_error(TimeoutError("ReadTimeout"))
    assert not rate_limit.is_transient_error(Exception("token invalid"))


def test_pro_call_retries_on_transient(monkeypatch):
    rate_limit.reset_for_tests()
    monkeypatch.setenv("XSHARE_TUSHARE_NET_RETRIES", "2")
    monkeypatch.setenv("XSHARE_TUSHARE_MIN_INTERVAL", "0.001")
    monkeypatch.setattr(tushare_source.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(rate_limit.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(tushare_source, "_use_https_direct", lambda: True)

    calls = {"n": 0}

    def fake_http_call(method, token, params, fields="", timeout=30.0):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionResetError(54, "Connection reset by peer")
        return pd.DataFrame({"ts_code": ["000001.SZ"]})

    monkeypatch.setattr(tushare_source, "_http_call", fake_http_call)
    df = tushare_source._pro_call("daily", trade_date="20260724")
    assert calls["n"] == 3
    assert not df.empty


def test_pro_call_retries_on_rate_limit(monkeypatch):
    rate_limit.reset_for_tests()
    monkeypatch.setenv("XSHARE_TUSHARE_RATE_RETRIES", "2")
    monkeypatch.setenv("XSHARE_TUSHARE_RATE_COOLDOWN", "1")
    monkeypatch.setenv("XSHARE_TUSHARE_MIN_INTERVAL", "0.001")
    monkeypatch.setattr(tushare_source.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(rate_limit.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(tushare_source, "_use_https_direct", lambda: True)

    calls = {"n": 0}

    def fake_http_call(method, token, params, fields="", timeout=30.0):
        calls["n"] += 1
        if calls["n"] < 3:
            raise Exception("抱歉，您访问接口(daily)频率超限(500次/分钟)")
        return pd.DataFrame({"ts_code": ["000001.SZ"]})

    monkeypatch.setattr(tushare_source, "_http_call", fake_http_call)
    df = tushare_source._pro_call("daily", trade_date="20260724")
    assert calls["n"] == 3
    assert not df.empty


def test_provider_daily_local_first_no_api(db_conn, monkeypatch):
    """有本地数据时不调用 failover。"""
    code = "000001.SZ"
    for i in range(40):
        d = date(2026, 1, 1) + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        db_conn.execute(
            "INSERT INTO stock_daily VALUES (?, ?, 10, 11, 9, 10.5, 1000, 10500, NULL)",
            [code, d],
        )

    mgr = ProviderManager()
    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("不应调用外部 API")

    monkeypatch.setattr(mgr, "_call_with_failover", boom)
    monkeypatch.setattr(mgr, "_get_latest_trade_date", lambda: "20260215")

    df = mgr.get_daily_history(code, days=60)
    assert not df.empty
    assert called["n"] == 0
    assert df.attrs.get("source") == "cache"


def test_provider_daily_empty_raises_without_force(db_conn, monkeypatch):
    mgr = ProviderManager()
    monkeypatch.setattr(mgr, "_get_latest_trade_date", lambda: "20260720")
    monkeypatch.setattr(
        mgr, "_call_with_failover", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no api"))
    )
    with pytest.raises(DataFetchError, match="本地无"):
        mgr.get_daily_history("000001.SZ", days=10)


def test_sync_stock_daily_writes_watermark(db_conn, monkeypatch):
    from tests.test_stock_daily import _FakePro, _make_tushare_daily_frame

    fake = _FakePro({"20260715": _make_tushare_daily_frame()})
    monkeypatch.setattr(tushare_source, "_get_pro", lambda: fake)
    monkeypatch.setenv("TUSHARE_TOKEN", "test")
    rate_limit.reset_for_tests()

    count = tushare_source.sync_stock_daily_to_db(trade_date="20260715", days=1)
    assert count == 3
    w = wm.get_watermark(wm.DATASET_DAILY, date(2026, 7, 15))
    assert w is not None
    assert w["status"] == "ok"
    assert w["row_count"] == 3


def test_sync_stock_basic_writes_watermark(db_conn, monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "test")
    rate_limit.reset_for_tests()

    def fake_fetch():
        return pd.DataFrame({
            "code": ["000001.SZ"],
            "name": ["平安银行"],
            "market": ["SZ"],
            "industry": ["银行"],
            "list_date": [date(1991, 4, 3)],
        })

    monkeypatch.setattr(tushare_source, "fetch_stock_basic", fake_fetch)
    n = tushare_source.sync_stock_basic_to_db(force=True)
    assert n == 1
    w = wm.get_watermark(wm.DATASET_STOCK_BASIC, "ALL")
    assert w["status"] == "ok"
