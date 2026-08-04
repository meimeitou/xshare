"""ETF 基础信息 / 日线同步单测。"""

from datetime import date

import pandas as pd

from xshare.data import sync_config
from xshare.data import watermark as wm
from xshare.data.sources import tushare_source as src


def test_sync_config_includes_etf_jobs(db_conn):
    sync_config.init_sync_config()
    jobs = {j["job"]: j for j in sync_config.get_all()}
    assert "etf_basic" in jobs
    assert "fund_daily" in jobs
    assert jobs["etf_basic"]["schedule"] == "interval"
    assert jobs["fund_daily"]["schedule"] == "calendar_1700"
    assert "fund_daily" in sync_config.CALENDAR_JOBS


def test_sync_etf_basic_to_db(db_conn, monkeypatch):
    fake = pd.DataFrame(
        [
            {
                "code": "510300.SH",
                "name": "沪深300ETF",
                "extname": "沪深300ETF",
                "index_code": "000300.SH",
                "index_name": "沪深300",
                "exchange": "SH",
                "mgr_name": "华泰柏瑞",
                "etf_type": "境内",
                "list_status": "L",
                "setup_date": date(2012, 5, 4),
                "list_date": date(2012, 5, 28),
            }
        ]
    )
    monkeypatch.setattr(src, "fetch_etf_basic", lambda list_status="L": fake)
    monkeypatch.setenv("TUSHARE_TOKEN", "x")

    n = src.sync_etf_basic_to_db(force=True)
    assert n == 1
    row = db_conn.execute(
        "SELECT code, name, index_code FROM etf_basic"
    ).fetchone()
    assert row == ("510300.SH", "沪深300ETF", "000300.SH")
    assert wm.get_watermark(wm.DATASET_ETF_BASIC, "ALL")["status"] == "ok"
    assert src.sync_etf_basic_to_db(force=False) == 0


def test_sync_fund_daily_by_trade_date(db_conn, monkeypatch):
    trade_day = date(2026, 7, 21)

    class FakePro:
        def fund_daily(self, **kwargs):
            assert kwargs.get("trade_date") == "20260721"
            return pd.DataFrame(
                [
                    {
                        "ts_code": "510300.SH",
                        "trade_date": "20260721",
                        "open": 4.0,
                        "high": 4.1,
                        "low": 3.9,
                        "close": 4.05,
                        "vol": 1000.0,
                        "amount": 5000.0,
                        "pct_chg": 1.2,
                    }
                ]
            )

    monkeypatch.setattr(src, "_get_pro", lambda: FakePro())
    monkeypatch.setattr(src, "_resolve_trade_days", lambda cursor, days, pro: [trade_day])
    monkeypatch.setenv("TUSHARE_TOKEN", "x")

    n = src.sync_fund_daily_to_db(days=1)
    assert n == 1
    row = db_conn.execute(
        "SELECT code, close, pct_chg FROM fund_daily WHERE trade_date = ?", [trade_day]
    ).fetchone()
    assert row == ("510300.SH", 4.05, 1.2)
    assert wm.get_watermark(wm.DATASET_FUND_DAILY, trade_day)["status"] == "ok"


def test_find_missing_fund_daily_dates(db_conn, monkeypatch):
    days = [date(2026, 7, 20), date(2026, 7, 21)]
    monkeypatch.setattr(src, "_resolve_trade_days", lambda cursor, n, pro: days)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    gaps = src.find_missing_fund_daily_dates(2)
    assert gaps == days

    wm.set_watermark(wm.DATASET_FUND_DAILY, days[0], wm.STATUS_OK, 1)
    assert src.find_missing_fund_daily_dates(2) == [days[1]]


def test_blocking_handlers_registered():
    assert sync_config.ETF_BASIC_JOB in sync_config._BLOCKING_HANDLERS
    assert sync_config.FUND_DAILY_JOB in sync_config._BLOCKING_HANDLERS
