"""个股资金流向（moneyflow）同步单测。"""

from datetime import date

import pandas as pd

from xshare.data import sync_config
from xshare.data import watermark as wm
from xshare.data.sources import tushare_source as src


def test_sync_config_includes_moneyflow_job(db_conn):
    sync_config.init_sync_config()
    jobs = {j["job"]: j for j in sync_config.get_all()}
    assert "moneyflow" in jobs
    assert jobs["moneyflow"]["schedule"] == "calendar_1700"
    assert "moneyflow" in sync_config.CALENDAR_JOBS


def test_sync_moneyflow_to_db(db_conn, monkeypatch):
    trade_day = date(2026, 7, 21)

    class FakePro:
        def moneyflow(self, **kwargs):
            assert kwargs.get("trade_date") == "20260721"
            return pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20260721",
                        "buy_sm_amount": 100.0,
                        "sell_sm_amount": 90.0,
                        "buy_md_amount": 200.0,
                        "sell_md_amount": 180.0,
                        "buy_lg_amount": 300.0,
                        "sell_lg_amount": 280.0,
                        "buy_elg_amount": 400.0,
                        "sell_elg_amount": 350.0,
                        "net_mf_amount": 100.0,
                    }
                ]
            )

    monkeypatch.setattr(src, "_get_pro", lambda: FakePro())
    monkeypatch.setattr(src, "_resolve_trade_days", lambda cursor, days, pro: [trade_day])
    monkeypatch.setenv("TUSHARE_TOKEN", "x")

    n = src.sync_moneyflow_to_db(days=1)
    assert n == 1
    row = db_conn.execute(
        "SELECT code, net_mf_amount FROM stock_moneyflow WHERE trade_date = ?",
        [trade_day],
    ).fetchone()
    assert row == ("000001.SZ", 100.0)
    assert wm.get_watermark(wm.DATASET_MONEYFLOW, trade_day)["status"] == "ok"


def test_find_missing_moneyflow_dates(db_conn, monkeypatch):
    days = [date(2026, 7, 20), date(2026, 7, 21)]
    monkeypatch.setattr(src, "_resolve_trade_days", lambda cursor, n, pro: days)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    gaps = src.find_missing_moneyflow_dates(2)
    assert gaps == days

    wm.set_watermark(wm.DATASET_MONEYFLOW, days[0], wm.STATUS_OK, 1)
    assert src.find_missing_moneyflow_dates(2) == [days[1]]


def test_blocking_handlers_registered():
    assert sync_config.MONEYFLOW_JOB in sync_config._BLOCKING_HANDLERS


def test_sync_moneyflow_range_mode(db_conn, monkeypatch):
    days = [date(2026, 7, 20), date(2026, 7, 21)]

    class FakePro:
        def moneyflow(self, **kwargs):
            td = kwargs.get("trade_date")
            return pd.DataFrame(
                [
                    {
                        "ts_code": f"00000{td[-1]}.SZ",
                        "trade_date": td,
                        "buy_sm_amount": 10.0,
                        "sell_sm_amount": 9.0,
                        "buy_md_amount": 20.0,
                        "sell_md_amount": 18.0,
                        "buy_lg_amount": 30.0,
                        "sell_lg_amount": 28.0,
                        "buy_elg_amount": 40.0,
                        "sell_elg_amount": 35.0,
                        "net_mf_amount": 10.0,
                    }
                ]
            )

    monkeypatch.setattr(src, "_get_pro", lambda: FakePro())
    monkeypatch.setattr(src, "_resolve_trade_days_range", lambda start_d, end_d, pro: days)
    monkeypatch.setenv("TUSHARE_TOKEN", "x")

    n = src.sync_moneyflow_to_db(
        start_date="2026-07-20", end_date="2026-07-21", overwrite=True,
    )
    assert n == 2
    count = db_conn.execute("SELECT COUNT(*) FROM stock_moneyflow").fetchone()[0]
    assert count == 2
    for d in days:
        assert wm.get_watermark(wm.DATASET_MONEYFLOW, d)["status"] == "ok"
