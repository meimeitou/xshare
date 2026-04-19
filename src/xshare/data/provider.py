"""统一数据 Provider 层 — 多源 failover + DuckDB 缓存 + TTL"""

from __future__ import annotations

import logging
import os
from abc import ABC
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from xshare.data.db import get_conn

logger = logging.getLogger(__name__)


# ─── 统一数据类型 ───────────────────────────────────────────

@dataclass
class RealtimeQuote:
    """实时行情统一结构"""
    code: str
    name: str = ""
    price: float = 0.0
    change_pct: float = 0.0
    change_amount: float = 0.0
    volume: int = 0
    amount: float = 0.0
    high: float = 0.0
    low: float = 0.0
    open: float = 0.0
    prev_close: float = 0.0
    turnover: float = 0.0
    pe: float | None = None
    pb: float | None = None
    total_mv: float | None = None
    source: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class MarketStats:
    """大盘涨跌统计"""
    total: int = 0
    up: int = 0
    down: int = 0
    flat: int = 0
    limit_up: int = 0
    limit_down: int = 0


@dataclass
class IndexQuote:
    """指数行情"""
    code: str
    name: str
    price: float = 0.0
    change_pct: float = 0.0


@dataclass
class SectorRank:
    """板块涨跌排行"""
    name: str
    change_pct: float = 0.0
    leader: str = ""          # 领涨股
    leader_pct: float = 0.0   # 领涨股涨幅


@dataclass
class TopMover:
    """涨跌幅 Top 个股"""
    code: str
    name: str
    price: float = 0.0
    change_pct: float = 0.0


# ─── Provider 抽象基类 ──────────────────────────────────────

class DataProvider(ABC):
    """数据源抽象接口 — 子类只需实现自己支持的方法"""

    name: str = "base"
    priority: int = 99  # 越小优先级越高

    def get_realtime_quote(self, code: str) -> RealtimeQuote:
        raise NotImplementedError

    def get_daily_history(self, code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """返回 DataFrame: code, trade_date, open, high, low, close, volume, amount, turnover"""
        raise NotImplementedError

    def get_stock_list(self) -> pd.DataFrame:
        """返回 DataFrame: code, name, [market, industry, list_date]"""
        raise NotImplementedError

    def get_financial_data(self, code: str) -> pd.DataFrame:
        """返回财务指标 DataFrame: code, end_date, pe, pb, roe, ..."""
        raise NotImplementedError

    def get_daily_basic(self, code: str, trade_date: str = "") -> pd.DataFrame:
        """每日指标（PE/PB/换手率等）"""
        raise NotImplementedError

    def get_fund_nav(self, code: str) -> pd.DataFrame:
        """基金净值: code, nav_date, nav, daily_return"""
        raise NotImplementedError

    def get_fund_basic(self, code: str) -> dict:
        """基金基本信息"""
        raise NotImplementedError

    def get_main_indices(self) -> list[IndexQuote]:
        """主要指数行情"""
        raise NotImplementedError

    def get_market_stats(self) -> MarketStats:
        """A 股涨跌统计"""
        raise NotImplementedError

    def get_sector_rankings(self, top_n: int = 5) -> tuple[list[SectorRank], list[SectorRank]]:
        """板块涨跌排行，返回 (涨幅前N, 跌幅前N)"""
        raise NotImplementedError

    def get_total_turnover(self) -> float:
        """两市总成交额（亿元）"""
        raise NotImplementedError

    def get_northbound_flow(self) -> dict:
        """北向资金净流入（亿元），返回 {total, sh_connect, sz_connect}"""
        raise NotImplementedError

    def get_top_movers(self, top_n: int = 5) -> tuple[list[TopMover], list[TopMover]]:
        """涨跌幅 Top N 个股，返回 (涨幅前N, 跌幅前N)"""
        raise NotImplementedError


# ─── Provider 注册与 Failover 管理 ──────────────────────────

class ProviderManager:
    """
    管理多个 DataProvider，按优先级 failover。
    同时集成 DuckDB 缓存 + TTL。
    """

    def __init__(self):
        self._providers: list[DataProvider] = []

    def register(self, provider: DataProvider):
        self._providers.append(provider)
        self._providers.sort(key=lambda p: p.priority)
        logger.info("Provider registered: %s (priority=%d)", provider.name, provider.priority)

    @property
    def providers(self) -> list[DataProvider]:
        return list(self._providers)

    def _call_with_failover(self, method: str, *args, **kwargs) -> Any:
        """按优先级逐个尝试，成功即返回"""
        errors = []
        for p in self._providers:
            fn = getattr(p, method, None)
            if fn is None:
                continue
            try:
                result = fn(*args, **kwargs)
                # 跳过空结果
                if result is None:
                    continue
                if isinstance(result, pd.DataFrame) and result.empty:
                    continue
                if isinstance(result, list) and len(result) == 0:
                    continue
                return result
            except NotImplementedError:
                continue
            except Exception as e:
                errors.append(f"{p.name}: {e}")
                logger.warning("Provider %s.%s failed: %s", p.name, method, e)
                continue
        raise DataFetchError(
            f"所有数据源 {method} 均失败: " + "; ".join(errors) if errors else f"无可用数据源支持 {method}"
        )

    # ─── 带缓存的公开接口 ──────────────────────────────────

    def get_realtime_quote(self, code: str) -> RealtimeQuote:
        """实时行情 — 不缓存"""
        return self._call_with_failover("get_realtime_quote", code)

    def get_daily_history(
        self, code: str, start_date: str | None = None, end_date: str | None = None, days: int = 120
    ) -> pd.DataFrame:
        """日线数据 — DuckDB 缓存 + TTL 1天"""
        now = datetime.now()
        if end_date is None:
            end_date = now.strftime("%Y%m%d")
        if start_date is None:
            start_date = (now - timedelta(days=days + 30)).strftime("%Y%m%d")

        conn = get_conn()
        df = conn.execute(
            "SELECT * FROM stock_daily WHERE code = ? AND trade_date >= ? ORDER BY trade_date",
            [code, start_date],
        ).fetchdf()

        # 检查缓存新鲜度：最新一条的日期距今 > 1 个交易日则刷新
        need_fetch = len(df) < 30
        if not need_fetch and not df.empty:
            latest = pd.to_datetime(df["trade_date"]).max().date()
            # 周末不算交易日，简单判断距今 > 3 天需要刷新
            if (date.today() - latest).days > 3:
                need_fetch = True

        if need_fetch:
            try:
                fresh = self._call_with_failover("get_daily_history", code, start_date, end_date)
                if not fresh.empty:
                    self._upsert_daily(conn, fresh)
                    df = fresh
            except DataFetchError:
                if df.empty:
                    raise
                logger.warning("刷新 %s 日线失败，使用缓存", code)

        return df

    def get_stock_list(self, force_refresh: bool = False) -> pd.DataFrame:
        """股票列表 — 缓存 7 天"""
        conn = get_conn()
        if not force_refresh:
            df = conn.execute("SELECT * FROM stock_basic").fetchdf()
            if not df.empty:
                if "updated_at" in df.columns:
                    latest = pd.to_datetime(df["updated_at"]).max()
                    if (datetime.now() - latest).days < 7:
                        return df
                else:
                    return df

        fresh = self._call_with_failover("get_stock_list")
        if not fresh.empty:
            self._upsert_stock_basic(conn, fresh)
        return fresh

    def get_financial_data(self, code: str) -> pd.DataFrame:
        """财务数据 — 缓存 1 天"""
        conn = get_conn()
        df = conn.execute(
            "SELECT * FROM stock_finance WHERE code = ? ORDER BY end_date DESC",
            [code],
        ).fetchdf()

        need_fetch = df.empty
        if not need_fetch and "updated_at" in df.columns:
            latest = pd.to_datetime(df["updated_at"]).max()
            if (datetime.now() - latest).days > 1:
                need_fetch = True

        if need_fetch:
            try:
                fresh = self._call_with_failover("get_financial_data", code)
                if not fresh.empty:
                    self._upsert_finance(conn, fresh)
                    df = fresh
            except DataFetchError:
                if df.empty:
                    raise

        return df

    def get_daily_basic(self, code: str, trade_date: str = "") -> pd.DataFrame:
        """每日基础指标（PE/PB 等）— 不缓存"""
        return self._call_with_failover("get_daily_basic", code, trade_date)

    def get_fund_nav(self, code: str) -> pd.DataFrame:
        """基金净值 — DuckDB 缓存 + TTL 1天"""
        conn = get_conn()
        df = conn.execute(
            "SELECT * FROM fund_nav WHERE code = ? ORDER BY nav_date",
            [code],
        ).fetchdf()

        need_fetch = df.empty
        if not need_fetch:
            latest = pd.to_datetime(df["nav_date"]).max().date()
            if (date.today() - latest).days > 1:
                need_fetch = True

        if need_fetch:
            try:
                fresh = self._call_with_failover("get_fund_nav", code)
                if not fresh.empty:
                    self._upsert_fund_nav(conn, fresh)
                    df = fresh
            except DataFetchError:
                if df.empty:
                    raise

        return df

    def get_fund_basic(self, code: str) -> dict:
        """基金基本信息 — DuckDB 缓存 + TTL 7天"""
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM fund_basic WHERE code = ?", [code]
        ).fetchone()

        need_fetch = row is None
        if not need_fetch:
            cols = [desc[0] for desc in conn.description]
            data = dict(zip(cols, row))
            if "updated_at" in data and data["updated_at"]:
                updated = pd.to_datetime(data["updated_at"])
                if (datetime.now() - updated).days > 7:
                    need_fetch = True
            if not need_fetch:
                for k, v in data.items():
                    if hasattr(v, "isoformat"):
                        data[k] = v.isoformat()
                return data

        try:
            info = self._call_with_failover("get_fund_basic", code)
            self._upsert_fund_basic(conn, info)
            return info
        except DataFetchError:
            if row is not None:
                cols = [desc[0] for desc in conn.description]
                data = dict(zip(cols, row))
                for k, v in data.items():
                    if hasattr(v, "isoformat"):
                        data[k] = v.isoformat()
                return data
            raise

    def get_main_indices(self) -> list[IndexQuote]:
        """主要指数 — 不缓存"""
        return self._call_with_failover("get_main_indices")

    def get_market_stats(self) -> MarketStats:
        """A 股涨跌统计 — 不缓存"""
        return self._call_with_failover("get_market_stats")

    def get_sector_rankings(self, top_n: int = 5) -> tuple[list[SectorRank], list[SectorRank]]:
        """板块涨跌排行 — 不缓存"""
        return self._call_with_failover("get_sector_rankings", top_n)

    def get_total_turnover(self) -> float:
        """两市总成交额 — 不缓存"""
        return self._call_with_failover("get_total_turnover")

    def get_northbound_flow(self) -> dict:
        """北向资金 — 不缓存"""
        return self._call_with_failover("get_northbound_flow")

    def get_top_movers(self, top_n: int = 5) -> tuple[list[TopMover], list[TopMover]]:
        """涨跌幅 Top N — 不缓存"""
        return self._call_with_failover("get_top_movers", top_n)

    # ─── DuckDB upsert 帮助方法 ─────────────────────────────

    @staticmethod
    def _upsert_daily(conn, df: pd.DataFrame):
        cols = ["code", "trade_date", "open", "high", "low", "close", "volume", "amount"]
        if "turnover" in df.columns:
            cols.append("turnover")
        df_insert = df[[c for c in cols if c in df.columns]]
        conn.execute(
            "INSERT INTO stock_daily SELECT * FROM df_insert "
            "ON CONFLICT (code, trade_date) DO UPDATE SET "
            "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
            "close=EXCLUDED.close, volume=EXCLUDED.volume, amount=EXCLUDED.amount"
        )

    @staticmethod
    def _upsert_stock_basic(conn, df: pd.DataFrame):
        for _, row in df.iterrows():
            conn.execute(
                "INSERT INTO stock_basic (code, name) VALUES (?, ?) "
                "ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, updated_at=current_timestamp",
                [row["code"], row["name"]],
            )

    @staticmethod
    def _upsert_finance(conn, df: pd.DataFrame):
        for _, row in df.iterrows():
            conn.execute(
                "INSERT INTO stock_finance (code, end_date, roe) VALUES (?, ?, ?) "
                "ON CONFLICT (code, end_date) DO UPDATE SET roe=EXCLUDED.roe, updated_at=current_timestamp",
                [row.get("code"), row.get("end_date"), row.get("roe")],
            )

    @staticmethod
    def _upsert_fund_nav(conn, df: pd.DataFrame):
        for _, row in df.iterrows():
            conn.execute(
                "INSERT INTO fund_nav (code, nav_date, nav) VALUES (?, ?, ?) "
                "ON CONFLICT (code, nav_date) DO UPDATE SET nav=EXCLUDED.nav",
                [row.get("code"), row.get("nav_date"), row.get("nav")],
            )

    @staticmethod
    def _upsert_fund_basic(conn, info: dict):
        conn.execute(
            "INSERT INTO fund_basic (code, name, fund_type, manager, setup_date) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (code) DO UPDATE SET "
            "name=EXCLUDED.name, fund_type=EXCLUDED.fund_type, "
            "manager=EXCLUDED.manager, updated_at=current_timestamp",
            [info.get("code"), info.get("name"), info.get("fund_type"),
             info.get("manager"), info.get("setup_date")],
        )


# ─── 异常 ───────────────────────────────────────────────────

class DataFetchError(Exception):
    """所有数据源均失败"""
    pass


# ─── 全局单例 ───────────────────────────────────────────────

_manager: ProviderManager | None = None


def get_provider() -> ProviderManager:
    """获取全局 ProviderManager 单例（惰性初始化，自动注册可用数据源）"""
    global _manager
    if _manager is None:
        _manager = ProviderManager()
        _auto_register(_manager)
    return _manager


def _auto_register(mgr: ProviderManager):
    """根据环境变量自动注册可用的数据源"""
    # AKShare — 免费，始终可用
    try:
        from xshare.data.sources.akshare_provider import AkShareProvider
        mgr.register(AkShareProvider())
    except Exception as e:
        logger.warning("AkShare provider 注册失败: %s", e)

    # Tushare — 需要 token
    if os.environ.get("TUSHARE_TOKEN"):
        try:
            from xshare.data.sources.tushare_provider import TushareProvider
            mgr.register(TushareProvider())
        except Exception as e:
            logger.warning("Tushare provider 注册失败: %s", e)
