from datetime import date, timedelta

import pandas as pd

from xshare.data.sources import tushare_source


def _make_tushare_daily_frame(trade_date: str = "20260715", n: int = 3, close_base: float = 10.0) -> pd.DataFrame:
    """构造 Tushare pro.daily(trade_date=) 的返回格式：ts_code/trade_date/ohlcv。"""
    return pd.DataFrame(
        {
            "ts_code": [f"00000{i}.SZ" for i in range(n)],
            "trade_date": [trade_date] * n,
            "open": [close_base - 0.5 + i for i in range(n)],
            "high": [close_base + 0.5 + i for i in range(n)],
            "low": [close_base - 1.0 + i for i in range(n)],
            "close": [close_base + i for i in range(n)],
            "vol": [100000 + i * 1000 for i in range(n)],
            "amount": [1.05e6 + i * 1e4 for i in range(n)],
        }
    )


class _FakePro:
    """模拟 tushare pro_api：按日期返回造数 DataFrame，并记录 API 调用。"""

    def __init__(self, frames_by_date: dict[str, pd.DataFrame] | None = None):
        self._frames = frames_by_date or {}
        self.calls: list[str] = []
        self.trade_cal_calls: list[tuple[str, str]] = []

    def daily(self, trade_date: str = "", **kwargs):
        self.calls.append(trade_date)
        if trade_date in self._frames:
            return self._frames[trade_date].copy()
        return pd.DataFrame()

    def trade_cal(self, exchange: str = "", start_date: str = "", end_date: str = "", is_open: str = "1"):
        self.trade_cal_calls.append((start_date, end_date))
        days = sorted(self._frames.keys())
        rows = [d for d in days if start_date <= d <= end_date]
        return pd.DataFrame({"cal_date": rows})


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def test_sync_stock_daily_upsert(db_conn, monkeypatch):
    """同步后 stock_daily 应包含对应行，字段已正确重命名。"""
    fake = _FakePro({"20260715": _make_tushare_daily_frame()})
    monkeypatch.setattr(tushare_source, "_get_pro", lambda: fake)

    count = tushare_source.sync_stock_daily_to_db(trade_date="20260715", days=1)
    assert count == 3

    rows = db_conn.execute("SELECT code, trade_date, close, volume FROM stock_daily ORDER BY code").fetchall()
    assert len(rows) == 3
    assert rows[0][0] == "000000.SZ"
    assert rows[0][1] == date(2026, 7, 15)
    assert rows[0][2] == 10.0
    assert rows[0][3] == 100000


def test_sync_stock_daily_idempotent(db_conn, monkeypatch):
    """重复同步不应新增行，第二次仅刷新最新交易日（1 次 API 调用）。"""
    fake = _FakePro({"20260715": _make_tushare_daily_frame()})
    monkeypatch.setattr(tushare_source, "_get_pro", lambda: fake)

    tushare_source.sync_stock_daily_to_db(trade_date="20260715", days=1)
    fake.calls.clear()
    tushare_source.sync_stock_daily_to_db(trade_date="20260715", days=1)

    rows = db_conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
    assert rows == 3
    assert len(fake.calls) == 1  # 仅刷新最新交易日


def test_sync_stock_daily_updates_on_conflict(db_conn, monkeypatch):
    """最新交易日重复同步应以最新值覆盖。"""
    monkeypatch.setattr(tushare_source, "_get_pro",
                        lambda: _FakePro({"20260715": _make_tushare_daily_frame(close_base=20.0)}))
    tushare_source.sync_stock_daily_to_db(trade_date="20260715", days=1)
    first = db_conn.execute("SELECT close FROM stock_daily WHERE code='000000.SZ'").fetchone()[0]

    monkeypatch.setattr(tushare_source, "_get_pro",
                        lambda: _FakePro({"20260715": _make_tushare_daily_frame(close_base=30.0)}))
    tushare_source.sync_stock_daily_to_db(trade_date="20260715", days=1)
    second = db_conn.execute("SELECT close FROM stock_daily WHERE code='000000.SZ'").fetchone()[0]

    assert second != first
    assert second == 30.0


def test_sync_stock_daily_incremental_skip(db_conn, monkeypatch):
    """--days 6 场景：5 天已在库中，仅拉取缺失的 1 天（最新交易日刷新）。"""
    # 预置 5 个交易日（07-07 ~ 07-11）已在库中
    for i in range(5):
        d = date(2026, 7, 7) + timedelta(days=i)
        db_conn.execute(
            "INSERT INTO stock_daily (code, trade_date, open, high, low, close, volume, amount) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ["000000.SZ", d, 10.0, 11.0, 9.0, 10.5, 100000, 1.05e6],
        )

    # FakePro 仅能提供 07-15 的数据
    fake = _FakePro({"20260715": _make_tushare_daily_frame()})
    monkeypatch.setattr(tushare_source, "_get_pro", lambda: fake)

    # 从 07-15 回溯 6 个交易日：07-15 拉取，07-11~07-07 跳过
    count = tushare_source.sync_stock_daily_to_db(trade_date="20260715", days=6)

    assert count == 3  # 仅 07-15 的 3 条新数据
    # API 只被调用过 07-15（07-14~07-12 非交易日返回空也算调用...）
    # 实际：07-15(fetch), 07-14(empty), 07-13(empty), 07-12(empty), 07-11(skip), 07-10(skip), 07-09(skip), 07-08(skip), 07-07(skip)
    fetched_dates = [c for c in fake.calls if c in ("20260715",)]
    assert "20260715" in fetched_dates
    # 已入库的 5 个交易日不应被 API 请求
    for i in range(5):
        d = date(2026, 7, 7) + timedelta(days=i)
        assert _ymd(d) not in fake.calls


def test_sync_stock_daily_days_30_repeat(db_conn, monkeypatch):
    """连续两次 --days 30：第二次大部分日期已在库中，仅刷新最新交易日。"""
    start = date(2026, 6, 15)
    frames = {}
    for i in range(30):
        d = start + timedelta(days=i)
        frames[_ymd(d)] = _make_tushare_daily_frame(trade_date=_ymd(d), n=2)

    fake = _FakePro(frames)
    monkeypatch.setattr(tushare_source, "_get_pro", lambda: fake)

    first = tushare_source.sync_stock_daily_to_db(trade_date=_ymd(start + timedelta(days=29)), days=30)
    assert first == 60  # 30 天 × 2 只股票

    fake.calls.clear()
    second = tushare_source.sync_stock_daily_to_db(trade_date=_ymd(start + timedelta(days=29)), days=30)
    assert second == 2  # 仅刷新最新交易日


def test_sync_stock_daily_uses_trade_calendar(db_conn, monkeypatch):
    """支持 trade_cal 时应仅请求交易日，避免非交易日空请求。"""
    frames = {
        "20260710": _make_tushare_daily_frame(trade_date="20260710", n=1),
        "20260711": _make_tushare_daily_frame(trade_date="20260711", n=1),
        "20260715": _make_tushare_daily_frame(trade_date="20260715", n=1),
    }
    fake = _FakePro(frames)
    monkeypatch.setattr(tushare_source, "_get_pro", lambda: fake)

    count = tushare_source.sync_stock_daily_to_db(trade_date="20260715", days=2)
    assert count == 2
    assert fake.trade_cal_calls, "应调用 trade_cal 获取交易日"
    assert fake.calls == ["20260715", "20260711"]


def test_sync_stock_daily_repulls_thin_date(db_conn, monkeypatch):
    """稀疏日（行数远少于 stock_basic）应重新拉取，不跳过。"""
    # 预置 100 只在市股票
    for i in range(100):
        db_conn.execute(
            "INSERT INTO stock_basic (code, name, list_date) VALUES (?, ?, ?)",
            [f"0000{i:03d}.SZ", f"S{i}", date(2020, 1, 1)],
        )
    # 2026-07-11 只有 2 行（稀疏日，阈值 95）
    for i in range(2):
        d = date(2026, 7, 11)
        db_conn.execute(
            "INSERT INTO stock_daily (code, trade_date, open, high, low, close, volume, amount) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [f"0000{i:03d}.SZ", d, 10.0, 11.0, 9.0, 10.5, 100000, 1.05e6],
        )

    # FakePro 提供 07-11 和 07-15 全市场数据
    frames = {
        "20260711": _make_tushare_daily_frame(trade_date="20260711", n=3),
        "20260715": _make_tushare_daily_frame(trade_date="20260715", n=3),
    }
    fake = _FakePro(frames)
    monkeypatch.setattr(tushare_source, "_get_pro", lambda: fake)

    # 07-15 最新强制刷新；07-11 稀疏日应重拉而非跳过
    tushare_source.sync_stock_daily_to_db(trade_date="20260715", days=2)
    assert "20260711" in fake.calls  # 稀疏日被重新拉取
