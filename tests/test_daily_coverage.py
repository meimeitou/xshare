"""日线覆盖率与启动入队回归测试。"""

from datetime import date, timedelta

import pandas as pd
import pytest

from xshare.data import sync_config, task_queue
from xshare.data.sources import tushare_source


def test_get_daily_coverage_empty(db_conn):
    cov = tushare_source.get_daily_coverage(lookback_trading_days=10)
    assert cov["trading_days_in_db"] == 0
    assert cov["target_days"] == 10
    assert cov["sufficient"] is False
    assert cov["missing_estimate"] == 10


def test_get_daily_coverage_with_data(db_conn):
    conn = db_conn
    conn.execute(
        """
        INSERT INTO stock_daily (code, trade_date, open, high, low, close, volume, amount)
        VALUES
            ('000001.SZ', '2026-07-01', 10, 11, 9, 10.5, 1000, 10500),
            ('000001.SZ', '2026-07-02', 10.5, 11.5, 10, 11, 1100, 12100),
            ('000002.SZ', '2026-07-01', 20, 21, 19, 20.5, 2000, 41000)
        """
    )
    from xshare.data.sources.tushare_source import _refresh_code_meta
    _refresh_code_meta(conn, "stock_daily", "stock", ["000001.SZ", "000002.SZ"])
    cov = tushare_source.get_daily_coverage(lookback_trading_days=2)
    assert cov["trading_days_in_db"] >= 2
    assert cov["sufficient"] is True


def test_enqueue_initial_jobs_no_autobackfill_when_empty(monkeypatch, db_conn):
    """库空时启动不应自动入队 daily backfill，历史补全由前端一次性接口触发。"""
    from xshare.data.sync_config import init_sync_config

    init_sync_config()
    monkeypatch.setattr(sync_config, "_daily_sync_window_open", lambda now=None: True)

    ids = task_queue.enqueue_initial_jobs()
    tasks = [task_queue.get_task(i) for i in ids]
    daily_tasks = [t for t in tasks if t and t["task_type"] == "daily"]
    # 库空 -> 不入队 daily 任务
    assert len(daily_tasks) == 0
    assert not any(t and t["task_type"] == "mainline" for t in tasks)


# ── 个股覆盖维度 ─────────────────────────────────────────────────────────────


def _seed_window(conn, tdays, code_rows: dict):
    """向 stock_basic + stock_daily 灌入测试数据。

    code_rows: {code: [要插入的 trade_date 下标]}，下标指向 tdays。
    """
    for code in code_rows:
        conn.execute(
            "INSERT OR IGNORE INTO stock_basic (code, name, list_date) VALUES (?, ?, ?)",
            [code, code, date(2020, 1, 1)],
        )
    cols = "code, trade_date, open, high, low, close, volume, amount"
    for code, idxs in code_rows.items():
        for i in idxs:
            d = tdays[i]
            conn.execute(
                f"INSERT INTO stock_daily ({cols}) VALUES (?,?,?,?,?,?,?,?)",
                [code, d, 10.0, 11.0, 9.0, 10.5, 100000, 1.05e6],
            )
    # 测试绕过 _upsert_*_daily，需手动刷新 code_meta 供覆盖率统计读取
    from xshare.data.sources.tushare_source import _refresh_code_meta
    _refresh_code_meta(conn, "stock_daily", "stock", list(code_rows.keys()))


def test_get_daily_coverage_per_stock_detects_thin(db_conn, monkeypatch):
    """市场维度达标但个股行数不足时，sufficient 必须为 False。"""
    from xshare.data.sources.tushare_source import _resolve_trade_days

    tdays = _resolve_trade_days(date.today(), 5, None)
    # 000001 满 5 天，000002 仅 1 天
    _seed_window(db_conn, tdays, {"000001.SZ": [0, 1, 2, 3, 4], "000002.SZ": [0]})

    cov = tushare_source.get_daily_coverage(lookback_trading_days=5)
    assert cov["trading_days_in_db"] >= 5
    assert cov["per_stock"]["total"] == 2
    assert cov["per_stock"]["sufficient"] is False  # 整体未达标
    assert cov["per_stock"]["under"] >= 1
    assert cov["sufficient"] is False


def test_get_daily_coverage_per_stock_all_sufficient(db_conn):
    from xshare.data.sources.tushare_source import _resolve_trade_days

    tdays = _resolve_trade_days(date.today(), 3, None)
    _seed_window(db_conn, tdays, {"000001.SZ": [0, 1, 2], "000002.SZ": [0, 1, 2]})
    cov = tushare_source.get_daily_coverage(lookback_trading_days=3)
    assert cov["per_stock"]["under"] == 0
    assert cov["sufficient"] is True


def test_backfill_thin_stocks_triggers_on_backfill_days(db_conn, monkeypatch):
    """days >= XSHARE_DAILY_BACKFILL_DAYS 时应触发个股补洞。"""
    called = {"n": 0}

    def fake_backfill(conn, cursor, pro, **kwargs):
        called["n"] += 1
        return 0

    monkeypatch.setattr(tushare_source, "_backfill_thin_codes", fake_backfill)

    class _FakePro:
        def daily(self, trade_date="", **kw):
            return pd.DataFrame()

        def trade_cal(self, **kw):
            return pd.DataFrame({"cal_date": []})

    monkeypatch.setattr(tushare_source, "_get_pro", lambda: _FakePro())
    try:
        tushare_source.sync_stock_daily_to_db(days=252)
    except Exception:
        pass
    assert called["n"] == 1


def test_backfill_thin_stocks_skipped_on_incremental(db_conn, monkeypatch):
    """days=1 增量不应触发个股补洞，避免每天扫全市场。"""
    called = {"n": 0}

    def fake_backfill(conn, cursor, pro, **kwargs):
        called["n"] += 1
        return 0

    monkeypatch.setattr(tushare_source, "_backfill_thin_codes", fake_backfill)

    class _FakePro:
        def daily(self, trade_date="", **kw):
            return pd.DataFrame()

        def trade_cal(self, **kw):
            return pd.DataFrame({"cal_date": []})

    monkeypatch.setattr(tushare_source, "_get_pro", lambda: _FakePro())
    try:
        tushare_source.sync_stock_daily_to_db(days=1)
    except Exception:
        pass
    assert called["n"] == 0


def test_backfill_thin_stocks_fills_missing_code(db_conn, monkeypatch):
    """个股补洞应按 code 区间拉取并写入缺失行。"""
    from xshare.data.sources.tushare_source import _resolve_trade_days

    tdays = _resolve_trade_days(date.today(), 252, None)
    # 000001 满数据；000002 仅 1 天
    _seed_window(db_conn, tdays, {"000001.SZ": list(range(len(tdays)))})
    db_conn.execute(
        "INSERT INTO stock_basic (code, name, list_date) VALUES ('000002.SZ','B','2020-01-01')"
    )
    # 仅给 000002 第 0 天
    _seed_window(db_conn, tdays, {"000002.SZ": [0]})

    # 构造 000002 全区间 Tushare 返回
    def fake_pro_call(method, **kw):
        if method != "daily":
            return pd.DataFrame()
        ts_code = kw.get("ts_code")
        # 多 code 批量调用：ts_code 可能是逗号分隔；逐只降级也是单 code
        codes = ts_code.split(",") if ts_code else []
        start = kw.get("start_date")
        end = kw.get("end_date")
        days = [d for d in tdays if start <= d.strftime("%Y%m%d") <= end]
        out_rows = []
        for c in codes:
            if c != "000002.SZ":
                continue
            for d in days:
                out_rows.append({
                    "ts_code": c, "trade_date": d.strftime("%Y%m%d"),
                    "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
                    "vol": 100000, "amount": 1.05e6,
                })
        return pd.DataFrame(out_rows)

    monkeypatch.setattr(tushare_source, "_pro_call", fake_pro_call)
    monkeypatch.setattr(tushare_source, "_get_pro", lambda: None)

    n = tushare_source._backfill_thin_codes(
        db_conn, date.today(), None,
        daily_table="stock_daily",
        basic_table="stock_basic",
        api_method="daily",
        upsert_fn=lambda df: tushare_source._upsert_stock_daily(db_conn, df),
        job_label="daily",
        threshold_env="XSHARE_DAILY_STOCK_THRESHOLD",
        dataset=tushare_source.wm.DATASET_DAILY,
    )
    assert n > 0
    rows = db_conn.execute(
        "SELECT COUNT(*) FROM stock_daily WHERE code='000002.SZ'"
    ).fetchone()[0]
    assert rows > 1


def test_sync_stock_daily_range_mode_overwrites(db_conn, monkeypatch):
    """start_date/end_date + overwrite 应强制重拉覆盖，跳过已有日期的逻辑失效。"""
    from xshare.data.sources.tushare_source import _resolve_trade_days

    tdays = _resolve_trade_days(date.today(), 5, None)
    # 预先写入两天数据
    _seed_window(db_conn, tdays, {"000001.SZ": [0, 1]})

    pulled_dates: list[str] = []

    def fake_pro_call(method, **kw):
        if method != "daily":
            return pd.DataFrame()
        yyyymmdd = kw.get("trade_date")
        pulled_dates.append(yyyymmdd)
        return pd.DataFrame([{
            "ts_code": "000001.SZ", "trade_date": yyyymmdd,
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
            "vol": 100000, "amount": 1.05e6,
        }])

    monkeypatch.setattr(tushare_source, "_pro_call", fake_pro_call)
    monkeypatch.setattr(tushare_source, "_get_pro", lambda: None)

    start_iso = min(tdays).isoformat()
    end_iso = max(tdays).isoformat()
    n = tushare_source.sync_stock_daily_to_db(
        start_date=start_iso, end_date=end_iso, overwrite=True,
    )
    # 区间内每个交易日都被拉取（overwrite 模式不跳过已有日期）
    assert len(pulled_dates) == len(tdays)
    assert n >= len(tdays)


def test_sync_stock_daily_range_mode_rejects_bad_range(db_conn, monkeypatch):
    """start_date 晚于 end_date 应报错。"""
    with pytest.raises(ValueError):
        tushare_source.sync_stock_daily_to_db(
            start_date="2026-07-31", end_date="2026-01-01",
        )
