"""共享测试夹具：内存 DuckDB（OLAP）+ 内存 SQLite（OLTP）+ 可配置的 FakeProvider。"""

import duckdb
import pandas as pd
import pytest
import sqlite3

from xshare.data import db as db_module
from xshare.data.sources import tushare_source
from xshare.data import sqlite_db as sqlite_db_module
from xshare.data.db import SCHEMA_SQL
from xshare.data.sqlite_db import SQLITE_SCHEMA_SQL
from xshare.data.provider import (
    IndexQuote,
    MarketStats,
    RealtimeQuote,
    SectorRank,
    TopMover,
)


# ─── 内存 DuckDB（OLAP：行情/财务/基金/新闻/stock_basic）──────────────


@pytest.fixture
def db_conn(monkeypatch):
    """提供一个已建表的内存 DuckDB 连接，所有 get_conn() 调用都会返回它。

    同时建好内存 SQLite OLTP 表（portfolio/sync_config/sync_task_queue），
    因为 portfolio 工具跨库读 stock_basic（DuckDB）+ 写 portfolio（SQLite），
    sync_job 工具写 sync_config/sync_task_queue（SQLite）。这样组合型测试
    只需请求 db_conn 即可同时拥有两库。

    db.py 已改为 per-call 连接（无缓存），故设置 db_module._test_conn 注入
    本 fixture 的共享内存连接，所有 get_conn() 调用返回它（含调用方在模块
    加载时 `from xshare.data.db import get_conn` 绑定的引用——get_conn 在
    调用时读 _test_conn，运行时求值，patch 生效）。
    """
    conn = duckdb.connect(":memory:")
    conn.execute(SCHEMA_SQL)
    monkeypatch.setattr(db_module, "_test_conn", conn)

    # 内存 SQLite OLTP 表（isolation_level=None 与生产一致，便于显式事务）
    sqlite_conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
    monkeypatch.setattr(sqlite_db_module, "_raw_conn", sqlite_conn)
    sqlite_conn.executescript(SQLITE_SCHEMA_SQL)

    yield conn
    conn.close()
    sqlite_conn.close()


@pytest.fixture
def sqlite_conn(monkeypatch):
    """纯 OLTP 夹具：只建内存 SQLite，不建 DuckDB。供 task_queue / sync_config
    单元测试使用（这些模块不碰 DuckDB）。"""
    conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
    monkeypatch.setattr(sqlite_db_module, "_raw_conn", conn)
    conn.executescript(SQLITE_SCHEMA_SQL)
    yield conn
    conn.close()


# ─── 数据构造辅助 ─────────────────────────────────────────────


def make_daily_history(code="002594.SZ", n=120, start=20.0):
    """构造 n 日日线 DataFrame，足够计算 MA60/趋势等指标。"""
    close = pd.Series([round(start + i * 0.1, 2) for i in range(n)])
    volume = pd.Series([100000 + i * 100 for i in range(n)])
    return pd.DataFrame(
        {
            "code": [code] * n,
            "trade_date": pd.date_range("2026-01-01", periods=n).date,
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": volume,
            "amount": (close * volume).round(2),
        }
    )


def make_financial_data(code="002594.SZ", quarters=6):
    """构造 quarters 期财务数据 DataFrame。"""
    dates = pd.date_range("2024-12-31", periods=quarters, freq="-3MS").date
    return pd.DataFrame(
        {
            "code": [code] * quarters,
            "end_date": dates,
            "pe": [25.0 + i for i in range(quarters)],
            "pb": [3.5 + 0.1 * i for i in range(quarters)],
            "roe": [15.0 - 0.5 * i for i in range(quarters)],
            "revenue": [100e8 + 10e8 * i for i in range(quarters)],
            "net_profit": [10e8 + 1e8 * i for i in range(quarters)],
            "revenue_yoy": [20.0 - i for i in range(quarters)],
            "profit_yoy": [25.0 - i for i in range(quarters)],
        }
    )


# ─── FakeProvider ─────────────────────────────────────────────


class FakeProvider:
    """覆盖工具所需全部方法的假数据源。"""

    name = "fake"
    priority = 1

    def __init__(self):
        self._history = make_daily_history()
        self._financial = make_financial_data()
        self._quote = RealtimeQuote(
            code="002594.SZ",
            name="比亚迪",
            price=250.0,
            change_pct=1.5,
            change_amount=3.7,
            volume=1000000,
            amount=2.5e8,
            high=252.0,
            low=246.0,
            open=247.0,
            prev_close=246.3,
            source="fake",
        )
        self._gainers = [
            TopMover(code="002594.SZ", name="比亚迪", price=250.0, change_pct=9.8),
            TopMover(code="300750.SZ", name="宁德时代", price=200.0, change_pct=7.2),
        ]
        self._losers = [
            TopMover(code="600519.SH", name="贵州茅台", price=1600.0, change_pct=-3.1),
        ]

    # 行情与历史
    def get_realtime_quote(self, code: str) -> RealtimeQuote:
        return self._quote

    def get_daily_history(self, code, start_date=None, end_date=None, days=120):
        return self._history

    def get_financial_data(self, code: str):
        return self._financial

    def get_stock_list(self):
        return pd.DataFrame(
            {
                "code": ["002594.SZ", "300750.SZ", "600519.SH"],
                "name": ["比亚迪", "宁德时代", "贵州茅台"],
                "industry": ["汽车", "电池", "白酒"],
            }
        )

    # 大盘
    def get_main_indices(self):
        return [
            IndexQuote(code="000001.SH", name="上证指数", price=3200.0, change_pct=1.2),
            IndexQuote(code="399001.SZ", name="深证成指", price=10100.0, change_pct=0.8),
            IndexQuote(code="399006.SZ", name="创业板指", price=2100.0, change_pct=1.0),
        ]

    def get_market_stats(self):
        return MarketStats(total=5000, up=3200, down=1500, flat=300, limit_up=80, limit_down=5)

    def get_total_turnover(self):
        return 12345.6

    def get_sector_rankings(self, top_n=5):
        top_up = [SectorRank(name="AI算力", change_pct=4.2, leader="中际旭创", leader_pct=8.1)]
        top_down = [SectorRank(name="房地产", change_pct=-2.1, leader="万科A", leader_pct=-3.0)]
        return top_up[:top_n], top_down[:top_n]

    def get_northbound_flow(self):
        return {"total": 25.6, "sh_connect": 13.2, "sz_connect": 12.4, "date": "20260419"}

    def get_top_movers(self, top_n=5):
        return self._gainers[:top_n], self._losers[:top_n]


@pytest.fixture
def fake_provider():
    return FakeProvider()


@pytest.fixture(autouse=True)
def _disable_tushare_https_direct(monkeypatch):
    """测试默认关闭 HTTPS 直连，使旧的 ``_get_pro``/FakePro 注入仍然生效。

    生产代码默认走 HTTPS 直连（``_http_call``），但大量旧测试通过
    ``monkeypatch.setattr(tushare_source, "_get_pro", lambda: FakePro())``
    注入桩件；若不关闭直连，这些测试会绕过桩件直接打真实 HTTP。
    """
    monkeypatch.setattr(tushare_source, "_use_https_direct", lambda: False)


class FailingProvider(FakeProvider):
    """所有数据方法均抛出异常，用于测试容错分支。"""

    def get_main_indices(self):
        raise RuntimeError("indices unavailable")

    def get_market_stats(self):
        raise RuntimeError("stats unavailable")

    def get_total_turnover(self):
        raise RuntimeError("turnover unavailable")

    def get_sector_rankings(self, top_n=5):
        raise RuntimeError("sector unavailable")

    def get_northbound_flow(self):
        raise RuntimeError("northbound unavailable")

    def get_top_movers(self, top_n=5):
        raise RuntimeError("movers unavailable")
