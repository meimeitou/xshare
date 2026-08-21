"""统一数据 Provider 层 — 多源 failover + DuckDB 缓存 + TTL"""

from __future__ import annotations

import logging
import os
import re
from abc import ABC
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from threading import Lock
from typing import Any

import pandas as pd

from xshare.data.db import get_conn

logger = logging.getLogger(__name__)


# ─── Provider 调用超时 ───────────────────────────────────────
# 所有数据源（akshare/tushare）的 HTTP 调用统一经此池执行并限时，
# 任一网络挂起都不会无限占用调用线程（也是 event loop 不被卡死的关键）。
_PROVIDER_CALL_TIMEOUT = float(os.environ.get("XSHARE_PROVIDER_TIMEOUT", "30"))
_provider_pool = ThreadPoolExecutor(max_workers=32)


# ─── 资产类型判断 ───────────────────────────────────────────

def detect_asset_type(code: str) -> str:
    """根据代码判断资产类型: stock / etf / index"""
    pure = code.split(".")[0]
    # ETF: 51xxxx(沪), 15xxxx(深), 56xxxx(深)
    if pure.startswith("51") or pure.startswith("56") or pure.startswith("159"):
        return "etf"
    # 指数: 000xxx.SH / 399xxx.SZ 等
    if pure.startswith("000") and code.endswith(".SH"):
        return "index"
    if pure.startswith("399"):
        return "index"
    return "stock"


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
    as_of: str = ""
    is_delayed: bool = False

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if v is not None}
        # 前端/REST 常用别名
        if "price" in d:
            d["current"] = d["price"]
        if "change_amount" in d:
            d["change"] = d["change_amount"]
        return d


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

    @staticmethod
    def _is_intraday_realtime_window(now: datetime | None = None) -> bool:
        """A 股盘中窗口：工作日 09:00-15:30（仅作标注参考，不再作为路由条件）。"""
        current = now or datetime.now()
        if current.weekday() >= 5:
            return False
        t = current.time()
        return time(9, 0) <= t <= time(15, 30)

    def _call_realtime_akshare_only(self, method: str, *args, **kwargs) -> Any:
        """当天实时类接口：仅 AkShare。

        Tushare 无实时权限，不参与实时读路径；个股失败由调用方回退本地库，
        大盘失败直接抛错由工具层返回 *_error。
        """
        return self._call_with_provider_names(["akshare"], method, *args, **kwargs)

    def _call_with_provider_names(self, provider_names: list[str], method: str, *args, **kwargs) -> Any:
        """仅在指定 provider 集合中按优先级尝试调用。

        ``_call_with_failover`` 是 ``provider_names=[所有]`` 的特例，统一走此实现。
        """
        allowed = set(provider_names)
        errors = []
        all_names = {p.name for p in self._providers}
        for p in self._providers:
            if p.name not in allowed:
                continue
            fn = getattr(p, method, None)
            if fn is None:
                continue
            try:
                result = _provider_pool.submit(fn, *args, **kwargs).result(
                    timeout=_PROVIDER_CALL_TIMEOUT
                )
                if result is None:
                    continue
                if isinstance(result, pd.DataFrame) and result.empty:
                    continue
                if isinstance(result, list) and len(result) == 0:
                    continue
                return result
            except TimeoutError:
                errors.append(f"{p.name}: timeout({_PROVIDER_CALL_TIMEOUT:g}s)")
                logger.warning(
                    "Provider %s.%s 超时（%ss），尝试下一个数据源",
                    p.name, method, _PROVIDER_CALL_TIMEOUT,
                )
                continue
            except NotImplementedError:
                continue
            except Exception as e:
                errors.append(f"{p.name}: {e}")
                logger.warning("Provider %s.%s failed: %s", p.name, method, e)
                continue

        if allowed == all_names:
            prefix = f"所有数据源 {method} 均失败"
        else:
            prefix = f"指定数据源 {method} 均失败"
        raise DataFetchError(
            prefix + ": " + "; ".join(errors) if errors else f"无可用数据源支持 {method}"
        )

    def _call_with_failover(self, method: str, *args, **kwargs) -> Any:
        """按优先级逐个尝试所有 provider，成功即返回。

        等价于 ``_call_with_provider_names([所有 provider 名], ...)``。
        """
        return self._call_with_provider_names(
            [p.name for p in self._providers], method, *args, **kwargs
        )

    # ─── 带缓存的公开接口 ──────────────────────────────────

    def _get_latest_trade_date(self) -> str:
        """获取最近的交易日 — 优先从缓存查询，然后从 API"""
        conn = get_conn()

        # 先从 stock_daily 缓存中查询最近的交易日
        try:
            df = conn.execute(
                "SELECT MAX(trade_date) as latest FROM stock_daily"
            ).fetchdf()
            if not df.empty:
                latest_value = df.iloc[0, 0]
                if latest_value is not None and not pd.isna(latest_value):
                    # 处理日期格式，可能是 date 或 datetime
                    latest = str(latest_value).split()[0].replace("-", "")
                    if re.fullmatch(r"\d{8}", latest):
                        logger.debug("从缓存获取最近交易日: %s", latest)
                        return latest
        except Exception as e:
            logger.debug("从缓存查询最近交易日失败: %s", e)

        # 缓存中没有数据，从 API 获取最近交易日
        try:
            for p in self._providers:
                if hasattr(p, "_latest_trade_date"):
                    latest = p._latest_trade_date()
                    if latest and re.fullmatch(r"\d{8}", str(latest)):
                        logger.debug("从 %s 获取最近交易日: %s", p.name, latest)
                        return str(latest)
        except Exception as e:
            logger.debug("从 API 查询最近交易日失败: %s", e)

        # 都失败了，返回当前日期
        now = datetime.now()
        return now.strftime("%Y%m%d")

    def get_realtime_quote(self, code: str) -> RealtimeQuote:
        """实时行情 — quote_snapshot 缓存优先；miss 走 AkShare（新浪）；再失败回退 stock_daily。"""
        cached = self._latest_quote_from_cache(code)
        if cached is not None:
            return cached
        try:
            quote = self._call_realtime_akshare_only("get_realtime_quote", code)
            if isinstance(quote, RealtimeQuote):
                quote.is_delayed = False
                if not quote.as_of:
                    quote.as_of = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return quote
        except DataFetchError:
            conn = get_conn()
            rows = conn.execute(
                """
                SELECT code, trade_date, open, high, low, close, volume, amount
                FROM stock_daily
                WHERE code = ?
                ORDER BY trade_date DESC
                LIMIT 2
                """,
                [code],
            ).fetchall()

            if not rows:
                raise

            latest = rows[0]
            latest_trade_date = str(latest[1]) if latest[1] is not None else ""
            prev_close = float(rows[1][5]) if len(rows) > 1 and rows[1][5] is not None else float(latest[5] or 0)
            price = float(latest[5] or 0)
            change_amount = price - prev_close
            change_pct = (change_amount / prev_close * 100.0) if prev_close else 0.0

            return RealtimeQuote(
                code=str(latest[0]),
                price=price,
                change_pct=round(change_pct, 4),
                change_amount=round(change_amount, 4),
                volume=int(latest[6] or 0),
                amount=float(latest[7] or 0),
                high=float(latest[3] or 0),
                low=float(latest[4] or 0),
                open=float(latest[2] or 0),
                prev_close=prev_close,
                source="cache",
                as_of=latest_trade_date,
                is_delayed=True,
            )

    @staticmethod
    def _normalize_stock_code(code: str) -> str:
        if "." not in code and re.match(r"^\d{6}$", code):
            if code.startswith("6") or code.startswith("5"):
                return f"{code}.SH"
            if code.startswith(("0", "3")):
                return f"{code}.SZ"
            if code.startswith(("4", "8")):
                return f"{code}.BJ"
        return code

    def get_daily_history(
        self,
        code: str,
        start_date: str | None = None,
        end_date: str | None = None,
        days: int = 120,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """日线数据 — 本地优先；默认不打外部 API（依赖 sync daily）。

        force_refresh=True 时经全局限速器拉取并回写。
        DataFrame.attrs 附带 source / as_of / is_stale / coverage_gap。
        """
        if end_date is None:
            end_date = self._get_latest_trade_date()

        if start_date is None:
            end_dt = datetime.strptime(end_date, "%Y%m%d")
            start_dt = end_dt - timedelta(days=days + 30)
            start_date = start_dt.strftime("%Y%m%d")

        code = self._normalize_stock_code(code)
        conn = get_conn()
        _fmt = "%Y%m%d" if "-" not in start_date else "%Y-%m-%d"
        db_start = datetime.strptime(start_date, _fmt).date()
        df = conn.execute(
            "SELECT * FROM stock_daily WHERE code = ? AND trade_date >= ? ORDER BY trade_date",
            [code, db_start],
        ).fetchdf()

        coverage_gap = False
        as_of = None
        if not df.empty:
            latest_ts = pd.to_datetime(df["trade_date"], errors="coerce").max()
            if not pd.isna(latest_ts):
                as_of = latest_ts.strftime("%Y%m%d")
                if as_of < end_date:
                    coverage_gap = True
                    logger.debug("%s: 本地最新 %s < 目标 %s（coverage_gap）", code, as_of, end_date)
        else:
            coverage_gap = True

        if force_refresh:
            try:
                fresh = self._call_with_failover("get_daily_history", code, start_date, end_date)
                if not fresh.empty:
                    self._upsert_daily(conn, fresh)
                    df = fresh
                    coverage_gap = False
                    latest_ts = pd.to_datetime(df["trade_date"], errors="coerce").max()
                    as_of = latest_ts.strftime("%Y%m%d") if not pd.isna(latest_ts) else as_of
            except DataFetchError:
                if df.empty:
                    raise
                logger.warning("force_refresh %s 日线失败，使用本地", code)

        if df.empty:
            raise DataFetchError(
                f"本地无 {code} 日线数据，请先在 /sync 页面执行日线同步（或 force_refresh=True）"
            )

        df.attrs["source"] = "api" if force_refresh and not coverage_gap else "cache"
        df.attrs["as_of"] = as_of
        df.attrs["is_stale"] = bool(coverage_gap)
        df.attrs["coverage_gap"] = bool(coverage_gap)
        return df

    def get_stock_list(self, force_refresh: bool = False) -> pd.DataFrame:
        """股票列表 — 本地优先；仅 force_refresh 时打 API。"""
        conn = get_conn()
        df = conn.execute("SELECT * FROM stock_basic").fetchdf()
        if not force_refresh and not df.empty:
            df.attrs["source"] = "cache"
            df.attrs["is_stale"] = False
            if "updated_at" in df.columns:
                latest = pd.to_datetime(df["updated_at"]).max()
                if not pd.isna(latest) and (datetime.now() - latest).days > 7:
                    df.attrs["is_stale"] = True
            return df

        if not force_refresh and df.empty:
            raise DataFetchError("本地无股票列表，请先在 /sync 页面执行股票列表同步（或 force_refresh=True）")

        fresh = self._call_with_failover("get_stock_list")
        if not fresh.empty:
            self._upsert_stock_basic(conn, fresh)
            fresh.attrs["source"] = "api"
            fresh.attrs["is_stale"] = False
            return fresh
        if not df.empty:
            df.attrs["source"] = "cache"
            df.attrs["is_stale"] = True
            return df
        raise DataFetchError("无法获取股票列表")

    def get_financial_data(self, code: str, force_refresh: bool = False) -> pd.DataFrame:
        """财务数据 — 本地优先。

        PE/PB 是日频指标，存在 ``stock_daily_basic`` 表（Tushare daily_basic
        同步）；``stock_finance`` 的 pe/pb 列因 fina_indicator 不返回而恒为
        NULL。此处从 stock_daily_basic 取最新交易日的 pe/pb 补进最新一期，
        使前端/AI 拿到非空 PE/PB。ROE/营收增速仍来自 stock_finance。
        """
        conn = get_conn()
        df = conn.execute(
            "SELECT * FROM stock_finance WHERE code = ? ORDER BY end_date DESC",
            [code],
        ).fetchdf()

        is_stale = False
        if not df.empty and "updated_at" in df.columns:
            latest = pd.to_datetime(df["updated_at"]).max()
            if not pd.isna(latest) and (datetime.now() - latest).days > 7:
                is_stale = True

        if force_refresh:
            try:
                fresh = self._call_with_failover("get_financial_data", code)
                if not fresh.empty:
                    self._upsert_finance(conn, fresh)
                    fresh.attrs["source"] = "api"
                    fresh.attrs["is_stale"] = False
                    self._backfill_pe_pb(conn, fresh)
                    return fresh
            except DataFetchError:
                if df.empty:
                    raise

        if df.empty:
            raise DataFetchError(
                f"本地无 {code} 财务数据，请先在 /sync 页面执行财务同步（或 force_refresh=True）"
            )

        self._backfill_pe_pb(conn, df)
        df.attrs["source"] = "cache"
        df.attrs["is_stale"] = is_stale
        return df

    @staticmethod
    def _backfill_pe_pb(conn, df: pd.DataFrame) -> None:
        """从 stock_daily_basic 取每只股票最新交易日的 pe/pb，就地填进 df 最新一期行。

        df 按 end_date DESC 排序（最新期在 iloc[0]）。daily_basic 的 pe/pb 是
        日频、按交易日存在，取该 code 最近一个交易日的一行即可。
        """
        if df.empty or "code" not in df.columns:
            return
        code = str(df.iloc[0]["code"])
        try:
            row = conn.execute(
                "SELECT pe, pb FROM stock_daily_basic "
                "WHERE code = ? ORDER BY trade_date DESC LIMIT 1",
                [code],
            ).fetchone()
        except Exception as exc:
            logger.debug("daily_basic pe/pb 回填失败 %s: %s", code, exc)
            return
        if not row:
            return
        pe_val, pb_val = row
        if pe_val is not None and "pe" in df.columns and pd.isna(df.iloc[0].get("pe")):
            df.iloc[0, df.columns.get_loc("pe")] = float(pe_val)
        if pb_val is not None and "pb" in df.columns and pd.isna(df.iloc[0].get("pb")):
            df.iloc[0, df.columns.get_loc("pb")] = float(pb_val)

    def get_daily_basic(self, code: str, trade_date: str = "", force_refresh: bool = False) -> pd.DataFrame:
        """每日基础指标 — 优先读 stock_daily_basic。"""
        conn = get_conn()
        code = self._normalize_stock_code(code)
        if trade_date:
            td = trade_date.replace("-", "")
            if len(td) == 8:
                db_date = datetime.strptime(td, "%Y%m%d").date()
            else:
                db_date = datetime.strptime(trade_date[:10], "%Y-%m-%d").date()
            df = conn.execute(
                "SELECT * FROM stock_daily_basic WHERE code = ? AND trade_date = ?",
                [code, db_date],
            ).fetchdf()
        else:
            df = conn.execute(
                "SELECT * FROM stock_daily_basic WHERE code = ? ORDER BY trade_date DESC LIMIT 30",
                [code],
            ).fetchdf()

        if not force_refresh and not df.empty:
            df.attrs["source"] = "cache"
            df.attrs["is_stale"] = False
            return df

        if force_refresh:
            fresh = self._call_with_failover("get_daily_basic", code, trade_date)
            fresh.attrs["source"] = "api"
            fresh.attrs["is_stale"] = False
            return fresh

        if df.empty:
            raise DataFetchError(
                f"本地无 {code} daily_basic，请先在 /sync 页面执行每日指标同步（或 force_refresh=True）"
            )
        df.attrs["source"] = "cache"
        return df

    def get_fund_basic(self, code: str, force_refresh: bool = False) -> dict:
        """基金基本信息 — 本地优先。"""
        conn = get_conn()
        res = conn.execute("SELECT * FROM fund_basic WHERE code = ?", [code])
        row = res.fetchone()
        cols = [d[0] for d in res.description] if row is not None else []

        def _row_dict():
            data = dict(zip(cols, row))
            for k, v in data.items():
                if hasattr(v, "isoformat"):
                    data[k] = v.isoformat()
            data["source"] = "cache"
            data["is_stale"] = False
            if data.get("updated_at"):
                updated = pd.to_datetime(data["updated_at"])
                if (datetime.now() - updated).days > 7:
                    data["is_stale"] = True
            return data

        if not force_refresh and row is not None:
            return _row_dict()

        if force_refresh or row is None:
            if force_refresh:
                try:
                    info = self._call_with_failover("get_fund_basic", code)
                    self._upsert_fund_basic(conn, info)
                    info["source"] = "api"
                    info["is_stale"] = False
                    return info
                except DataFetchError:
                    if row is not None:
                        return _row_dict()
                    raise
            if row is None:
                raise DataFetchError(
                    f"本地无 {code} 基金信息，请先同步或 force_refresh=True"
                )
        return _row_dict()

    # ─── 实时读路径：quote_snapshot 缓存优先，miss/异常回退 AkShare 实时 ──
    # quote 同步任务（交易时段每 5 分钟）已把新浪快照写入 DuckDB；
    # 页面/工具经这里默认吃缓存，缓存为空（未同步过）才打实时接口。

    @staticmethod
    def _latest_quote_from_cache(code: str) -> RealtimeQuote | None:
        from xshare.data import quote_cache
        try:
            row = quote_cache.latest_quote(code)
        except Exception as exc:
            logger.debug("quote_snapshot 读取失败: %s", exc)
            return None
        if not row:
            return None
        return RealtimeQuote(
            code=code,
            name=str(row.get("name") or ""),
            price=float(row.get("price") or 0),
            change_pct=float(row.get("change_pct") or 0),
            change_amount=float(row.get("change_amount") or 0),
            volume=int(row.get("volume") or 0),
            amount=float(row.get("amount") or 0),
            high=float(row.get("high") or 0),
            low=float(row.get("low") or 0),
            open=float(row.get("open") or 0),
            prev_close=float(row.get("prev_close") or 0),
            turnover=float(row["turnover"]) if row.get("turnover") is not None else 0.0,
            pe=float(row["pe"]) if row.get("pe") is not None else None,
            pb=float(row["pb"]) if row.get("pb") is not None else None,
            total_mv=float(row["total_mv"]) if row.get("total_mv") is not None else None,
            source="quote_cache",
            as_of=str(row.get("ts") or ""),
            is_delayed=False,
        )

    @staticmethod
    def _latest_spot_df() -> pd.DataFrame | None:
        from xshare.data import quote_cache
        try:
            df = quote_cache.latest_spot()
        except Exception as exc:
            logger.debug("quote_snapshot 读取失败: %s", exc)
            return None
        return df if not df.empty else None

    def get_main_indices(self) -> list[IndexQuote]:
        """主要指数 — index_snapshot 缓存优先；miss 回退 AkShare。"""
        from xshare.data import quote_cache
        target = {
            "上证指数": "000001",
            "深证成指": "399001",
            "创业板指": "399006",
            "科创50": "000688",
        }
        try:
            rows = [r for r in quote_cache.latest_indices() if r.get("name") in target]
            if rows:
                return [
                    IndexQuote(
                        code=target[r["name"]],
                        name=r["name"],
                        price=float(r.get("price") or 0),
                        change_pct=float(r.get("change_pct") or 0),
                    )
                    for r in rows
                ]
        except Exception as exc:
            logger.debug("index_snapshot 读取失败: %s", exc)
        return self._call_realtime_akshare_only("get_main_indices")

    def get_market_stats(self) -> MarketStats:
        """A 股涨跌统计 — quote_snapshot 缓存优先；miss 回退 AkShare。"""
        df = self._latest_spot_df()
        if df is not None:
            total = len(df)
            up = len(df[df["change_pct"] > 0])
            down = len(df[df["change_pct"] < 0])
            return MarketStats(
                total=total, up=up, down=down, flat=total - up - down,
                limit_up=len(df[df["change_pct"] >= 9.9]),
                limit_down=len(df[df["change_pct"] <= -9.9]),
            )
        return self._call_realtime_akshare_only("get_market_stats")

    def get_sector_rankings(self, top_n: int = 5) -> tuple[list[SectorRank], list[SectorRank]]:
        """板块涨跌排行 — sector_snapshot 缓存优先；miss 回退 AkShare。"""
        from xshare.data import quote_cache
        try:
            rows = sorted(
                quote_cache.latest_sectors(),
                key=lambda r: r.get("change_pct") or 0,
                reverse=True,
            )
            if rows:
                def _mk(r: dict) -> SectorRank:
                    return SectorRank(
                        name=str(r.get("name") or ""),
                        change_pct=float(r.get("change_pct") or 0),
                        leader=str(r.get("leader") or ""),
                        leader_pct=float(r.get("leader_pct") or 0),
                    )
                return [_mk(r) for r in rows[:top_n]], [_mk(r) for r in rows[-top_n:]]
        except Exception as exc:
            logger.debug("sector_snapshot 读取失败: %s", exc)
        return self._call_realtime_akshare_only("get_sector_rankings", top_n)

    def get_total_turnover(self) -> float:
        """两市总成交额 — quote_snapshot 缓存优先；miss 回退 AkShare。"""
        df = self._latest_spot_df()
        if df is not None:
            return round(float(df["amount"].sum()) / 1e8, 2)
        return self._call_realtime_akshare_only("get_total_turnover")

    def get_northbound_flow(self) -> dict:
        """北向资金净流入 — Tushare moneyflow_hsgt（日终数据，非盘中实时）。"""
        return self._call_with_provider_names(["tushare"], "get_northbound_flow")

    def get_top_movers(self, top_n: int = 5) -> tuple[list[TopMover], list[TopMover]]:
        """涨跌幅 Top N — quote_snapshot 缓存优先；miss 回退 AkShare。"""
        df = self._latest_spot_df()
        if df is not None:
            df = df.sort_values("change_pct", ascending=False)
            def _mk(r) -> TopMover:
                return TopMover(
                    code=str(r["code"]), name=str(r["name"]),
                    price=float(r["price"]), change_pct=float(r["change_pct"]),
                )
            return (
                [_mk(r) for _, r in df.head(top_n).iterrows()],
                [_mk(r) for _, r in df.tail(top_n).iterrows()],
            )
        return self._call_realtime_akshare_only("get_top_movers", top_n)

    # ─── DuckDB upsert 帮助方法 ─────────────────────────────

    @staticmethod
    def _upsert_daily(conn, df: pd.DataFrame):
        from xshare.data.sources.tushare_source import _upsert_stock_daily
        cols = ["code", "trade_date", "open", "high", "low", "close", "volume", "amount"]
        missing = [c for c in cols if c not in df.columns]
        if missing:
            raise ValueError(f"stock_daily upsert 缺少列: {missing}")
        _upsert_stock_daily(conn, df[cols])

    @staticmethod
    def _upsert_stock_basic(conn, df: pd.DataFrame):
        for _, row in df.iterrows():
            conn.execute(
                "INSERT INTO stock_basic (code, name) VALUES (?, ?) "
                "ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, updated_at=now()",
                [row["code"], row["name"]],
            )

    @staticmethod
    def _upsert_finance(conn, df: pd.DataFrame):
        # 项目未发布阶段，直接按当前 schema 写入全字段，避免 fundamentals 缺字段。
        sql = (
            "INSERT INTO stock_finance "
            "(code, end_date, pe, pb, roe, revenue, net_profit, revenue_yoy, profit_yoy) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (code, end_date) DO UPDATE SET "
            "pe=EXCLUDED.pe, pb=EXCLUDED.pb, roe=EXCLUDED.roe, "
            "revenue=EXCLUDED.revenue, net_profit=EXCLUDED.net_profit, "
            "revenue_yoy=EXCLUDED.revenue_yoy, profit_yoy=EXCLUDED.profit_yoy, "
            "updated_at=now()"
        )
        for _, row in df.iterrows():
            conn.execute(
                sql,
                [
                    row.get("code"),
                    row.get("end_date"),
                    row.get("pe"),
                    row.get("pb"),
                    row.get("roe"),
                    row.get("revenue"),
                    row.get("net_profit"),
                    row.get("revenue_yoy"),
                    row.get("profit_yoy"),
                ],
            )

    @staticmethod
    def _upsert_fund_basic(conn, info: dict):
        conn.execute(
            "INSERT INTO fund_basic (code, name, fund_type, manager, setup_date) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (code) DO UPDATE SET "
            "name=EXCLUDED.name, fund_type=EXCLUDED.fund_type, "
            "manager=EXCLUDED.manager, updated_at=now()",
            [info.get("code"), info.get("name"), info.get("fund_type"),
             info.get("manager"), info.get("setup_date")],
        )


# ─── 异常 ───────────────────────────────────────────────────

class DataFetchError(Exception):
    """所有数据源均失败"""
    pass


# ─── 全局单例 ───────────────────────────────────────────────

_manager: ProviderManager | None = None
_manager_lock = Lock()


def get_provider() -> ProviderManager:
    """获取全局 ProviderManager 单例（惰性初始化，线程安全，自动注册可用数据源）"""
    global _manager
    if _manager is None:
        with _manager_lock:
            # 双重检查，避免多个线程同时进入临界区重复创建
            if _manager is None:
                mgr = ProviderManager()
                _auto_register(mgr)
                _manager = mgr
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
