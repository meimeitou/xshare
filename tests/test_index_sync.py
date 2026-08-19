"""指数基础信息 / 日线同步单测。"""

from datetime import date

import pandas as pd

from xshare.data import watermark as wm
from xshare.data.sources import tushare_source as src
from xshare.data import sync_config


def test_sync_config_includes_index_jobs(db_conn):
    sync_config.init_sync_config()
    jobs = {j["job"]: j for j in sync_config.get_all()}
    assert "index_basic" in jobs
    assert "index_daily" in jobs
    assert jobs["index_basic"]["schedule"] == "interval"
    assert jobs["index_daily"]["schedule"] == "calendar_1600"
    assert "index_daily" in sync_config.CALENDAR_JOBS


def test_sync_index_basic_to_db(db_conn, monkeypatch):
    fake = pd.DataFrame(
        [
            {
                "code": "000001.SH",
                "name": "上证指数",
                "market": "SSE",
                "publisher": "SSE",
                "category": "综合指数",
                "base_date": date(1990, 12, 19),
                "list_date": date(1991, 7, 15),
            },
            {
                "code": "399001.SZ",
                "name": "深证成指",
                "market": "SZSE",
                "publisher": "SZSE",
                "category": "综合指数",
                "base_date": date(1994, 7, 20),
                "list_date": date(1995, 1, 23),
            },
        ]
    )
    monkeypatch.setattr(src, "fetch_index_basic", lambda markets=None: fake)
    monkeypatch.setenv("TUSHARE_TOKEN", "x")

    n = src.sync_index_basic_to_db(force=True)
    assert n == 2
    rows = db_conn.execute("SELECT code, name FROM index_basic ORDER BY code").fetchall()
    assert rows == [("000001.SH", "上证指数"), ("399001.SZ", "深证成指")]
    assert wm.get_watermark(wm.DATASET_INDEX_BASIC, "ALL")["status"] == "ok"

    # 24h 内再次同步应跳过
    assert src.sync_index_basic_to_db(force=False) == 0


def test_sync_index_daily_to_db(db_conn, monkeypatch):
    db_conn.execute(
        "INSERT INTO index_basic (code, name, market) VALUES ('000001.SH', '上证指数', 'SSE')"
    )
    trade_day = date(2026, 7, 21)

    class FakePro:
        def index_daily(self, **kwargs):
            # 优先 trade_date 全市场拉取路径
            if kwargs.get("trade_date"):
                return pd.DataFrame([{
                    "ts_code": "000001.SH",
                    "trade_date": kwargs["trade_date"],
                    "open": 3200.0, "high": 3210.0, "low": 3190.0,
                    "close": 3205.0, "vol": 1e8, "amount": 2e8, "pct_chg": 0.5,
                }])
            ts_code = kwargs.get("ts_code")
            return pd.DataFrame([{
                "ts_code": ts_code, "trade_date": "20260721",
                "open": 3200.0, "high": 3210.0, "low": 3190.0,
                "close": 3205.0, "vol": 1e8, "amount": 2e8, "pct_chg": 0.5,
            }])

    monkeypatch.setattr(src, "_get_pro", lambda: FakePro())
    monkeypatch.setattr(src, "_resolve_trade_days", lambda cursor, days, pro: [trade_day])
    monkeypatch.setenv("TUSHARE_TOKEN", "x")

    n = src.sync_index_daily_to_db(days=1)
    assert n == 1
    row = db_conn.execute(
        "SELECT code, close, pct_chg FROM index_daily WHERE trade_date = ?", [trade_day]
    ).fetchone()
    assert row == ("000001.SH", 3205.0, 0.5)
    assert wm.get_watermark(wm.DATASET_INDEX_DAILY, trade_day)["status"] == "ok"


def test_find_missing_index_daily_dates(db_conn, monkeypatch):
    days = [date(2026, 7, 20), date(2026, 7, 21)]
    monkeypatch.setattr(src, "_resolve_trade_days", lambda cursor, n, pro: days)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    gaps = src.find_missing_index_daily_dates(2)
    assert gaps == days

    wm.set_watermark(wm.DATASET_INDEX_DAILY, days[0], wm.STATUS_OK, 1)
    gaps2 = src.find_missing_index_daily_dates(2)
    assert gaps2 == [days[1]]


def test_blocking_handlers_registered():
    assert sync_config.INDEX_BASIC_JOB in sync_config._BLOCKING_HANDLERS
    assert sync_config.INDEX_DAILY_JOB in sync_config._BLOCKING_HANDLERS


def test_sync_index_daily_multi_code_batching(db_conn, monkeypatch):
    """单日场景：trade_date 全市场拉取一次覆盖所有 code。"""
    db_conn.execute(
        "INSERT INTO index_basic (code, name, market) VALUES "
        "('000001.SH','上证指数','SSE'),"
        "('399001.SZ','深证成指','SZSE'),"
        "('399006.SZ','创业板指','SZSE')"
    )
    trade_day = date(2026, 7, 21)
    trade_date_calls: list[str] = []
    ts_code_calls: list[str] = []

    class FakePro:
        def index_daily(self, **kwargs):
            if kwargs.get("trade_date"):
                trade_date_calls.append(kwargs["trade_date"])
                return pd.DataFrame([
                    {"ts_code": c, "trade_date": kwargs["trade_date"],
                     "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                     "vol": 1.0, "amount": 1.0, "pct_chg": 0.0}
                    for c in ("000001.SH", "399001.SZ", "399006.SZ")
                ])
            ts_code_calls.append(kwargs.get("ts_code"))
            return pd.DataFrame()

    monkeypatch.setattr(src, "_get_pro", lambda: FakePro())
    monkeypatch.setattr(src, "_resolve_trade_days", lambda cursor, days, pro: [trade_day])
    monkeypatch.setenv("TUSHARE_TOKEN", "x")

    n = src.sync_index_daily_to_db(days=1)
    assert n == 3
    # trade_date 路径一次拉全，不再回退到逐只
    assert len(trade_date_calls) == 1
    assert ts_code_calls == []
    assert wm.get_watermark(wm.DATASET_INDEX_DAILY, trade_day)["status"] == "ok"


def test_sync_index_daily_batch_fallback_to_single(db_conn, monkeypatch):
    """trade_date 调用返回空时回退逐只拉取，不丢数据。"""
    db_conn.execute(
        "INSERT INTO index_basic (code, name, market) VALUES "
        "('000001.SH','上证指数','SSE'),"
        "('399001.SZ','深证成指','SZSE')"
    )
    trade_day = date(2026, 7, 21)

    class FakePro:
        def index_daily(self, **kwargs):
            # trade_date 全市场路径返回空，触发回退
            if kwargs.get("trade_date"):
                return pd.DataFrame()
            ts_code = kwargs.get("ts_code")
            return pd.DataFrame([{
                "ts_code": ts_code, "trade_date": "20260721",
                "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                "vol": 1.0, "amount": 1.0, "pct_chg": 0.0,
            }])

    monkeypatch.setattr(src, "_get_pro", lambda: FakePro())
    monkeypatch.setattr(src, "_resolve_trade_days", lambda cursor, days, pro: [trade_day])
    monkeypatch.setenv("TUSHARE_TOKEN", "x")

    n = src.sync_index_daily_to_db(days=1)
    # trade_date 空后逐只补回，2 条都入库
    assert n == 2
