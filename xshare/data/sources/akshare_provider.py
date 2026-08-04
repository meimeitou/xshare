"""AkShare 数据源 Provider"""

import logging
import os
import threading
import time
from functools import wraps
from typing import Callable

import akshare as ak
import pandas as pd

from xshare.data.provider import (
    DataProvider,
    IndexQuote,
    MarketStats,
    RealtimeQuote,
    SectorRank,
    TopMover,
    detect_asset_type,
)

logger = logging.getLogger(__name__)


# ─── 网络重试：akshare 内部 retry 不捕获 ConnectionError/RemoteDisconnected ──

def _ak_retry(fn):
    """包裹 akshare 调用，对瞬时连接错误（RemoteDisconnected/ConnectionError
    等 OSError 子类）做指数退避重试。

    akshare 的 request_with_retry 只捕获 requests.RequestException，
    而 http.client.RemoteDisconnected 走 ConnectionError → OSError 分支，
    会直接穿透导致整次调用失败。此处补齐。
    """
    retries = int(os.environ.get("XSHARE_AKSHARE_RETRIES", "2") or "2")
    base_delay = float(os.environ.get("XSHARE_AKSHARE_RETRY_DELAY", "0.5") or "0.5")

    @wraps(fn)
    def wrapper(*args, **kwargs):
        last_exc = None
        for attempt in range(retries + 1):
            try:
                return fn(*args, **kwargs)
            except (ConnectionError, OSError, TimeoutError) as exc:
                last_exc = exc
                if attempt < retries:
                    delay = base_delay * (2 ** attempt)
                    logger.debug(
                        "akshare %s 瞬时错误(%s)，%.1fs 后重试 %d/%d",
                        getattr(fn, "__name__", fn), exc, delay, attempt + 1, retries,
                    )
                    time.sleep(delay)
                    continue
                raise
        raise last_exc  # type: ignore[misc]

    return wrapper


# ─── 短期内存缓存：同一请求窗口内复用全市场快照 ───────────────────────────────
# market_mainline / market_overview 一次调用会触发 get_market_stats +
# get_total_turnover + get_top_movers，三者各自调 stock_zh_a_spot_em()
# 拉取全 A 股快照（58 页、约 5800 行）。短缓存让它们共享一次拉取，
# 盘中 30s 内不重复请求同一重数据。

_CACHE_TTL = float(os.environ.get("XSHARE_AKSHARE_SPOT_TTL", "30") or "30")
_spot_cache_lock = threading.Lock()
_spot_cache: dict[str, tuple[float, pd.DataFrame]] = {}


def _cached_spot_em() -> pd.DataFrame:
    """带 TTL 的全 A 股快照缓存。"""
    now = time.time()
    with _spot_cache_lock:
        entry = _spot_cache.get("spot")
        if entry and (now - entry[0]) < _CACHE_TTL:
            logger.debug("复用 stock_zh_a_spot_em 缓存（age=%.1fs）", now - entry[0])
            return entry[1].copy()
    df = _ak_retry(ak.stock_zh_a_spot_em)()
    if df is None or df.empty:
        return df
    with _spot_cache_lock:
        _spot_cache["spot"] = (time.time(), df)
    return df.copy()


def _cached_index_spot_em() -> pd.DataFrame:
    """带 TTL 的指数快照缓存。"""
    now = time.time()
    with _spot_cache_lock:
        entry = _spot_cache.get("index")
        if entry and (now - entry[0]) < _CACHE_TTL:
            return entry[1].copy()
    df = _ak_retry(ak.stock_zh_index_spot_em)()
    if df is None or df.empty:
        return df
    with _spot_cache_lock:
        _spot_cache["index"] = (time.time(), df)
    return df.copy()


def _cached_generic(key: str, fetch: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    """带 TTL 的通用快照缓存，供板块/北向等独立 HTTP 接口复用。

    与 _cached_spot_em 同样的锁/过期机制，避免每次大盘概览都裸打 akshare。
    """
    now = time.time()
    with _spot_cache_lock:
        entry = _spot_cache.get(key)
        if entry and (now - entry[0]) < _CACHE_TTL:
            return entry[1].copy()
    df = fetch()
    if df is None or df.empty:
        return df
    with _spot_cache_lock:
        _spot_cache[key] = (time.time(), df)
    return df.copy()



def _to_minute_symbol(code: str) -> str:
    """将标准化代码 (如 002594.SZ / 000001.SH / 510300.SH) 转为
    stock_zh_a_minute 所需的 symbol 格式 (如 sz002594 / sh000001)。"""
    pure, _, suffix = code.partition(".")
    suffix = suffix.upper()
    if suffix in ("SH", "SZ", "BJ"):
        return f"{suffix.lower()}{pure}"
    # 无后缀时按首位数字推断交易所
    if pure.startswith(("6", "5", "9", "11")):
        return f"sh{pure}"
    if pure.startswith(("0", "2", "3")):
        return f"sz{pure}"
    if pure.startswith(("4", "8")):
        return f"bj{pure}"
    return f"sh{pure}"


class AkShareProvider(DataProvider):
    name = "akshare"
    priority = 1  # 默认优先级，可通过环境变量覆盖

    def __init__(self):
        import os
        self.priority = int(os.getenv("AKSHARE_PRIORITY", "1"))

    # ─── 实时行情 ────────────────────────────────────────────

    def get_realtime_quote(self, code: str) -> RealtimeQuote:
        """基于 stock_zh_a_minute 分钟线获取实时行情（新浪源，免费）。

        取最新一根 15 分钟 bar 的 close 作为当前价，并从分钟序列里
        推算当日 open/high/low/volume/amount 与前一交易日收盘价，
        进而计算涨跌额/涨跌幅。name/turnover/pe/pb 在此源下缺失。
        """
        symbol = _to_minute_symbol(code)
        df = _ak_retry(ak.stock_zh_a_minute)(symbol=symbol, period="15", adjust="")
        if df is None or df.empty:
            raise ValueError(f"未找到股票: {code}")

        # 数据列默认均为字符串，统一转数值
        for col in ("open", "high", "low", "close", "volume", "amount"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["day"] = pd.to_datetime(df["day"], errors="coerce")
        df = df.dropna(subset=["close"]).reset_index(drop=True)

        latest = df.iloc[-1]
        price = float(latest["close"])
        today = latest["day"].date()
        today_bars = df[df["day"].dt.date == today]
        prev_bars = df[df["day"].dt.date < today]

        open_ = float(today_bars.iloc[0]["open"]) if not today_bars.empty else price
        high = float(today_bars["high"].max()) if not today_bars.empty else price
        low = float(today_bars["low"].min()) if not today_bars.empty else price
        volume = int(today_bars["volume"].sum()) if not today_bars.empty else 0
        amount = float(today_bars["amount"].sum()) if not today_bars.empty else 0.0
        prev_close = float(prev_bars.iloc[-1]["close"]) if not prev_bars.empty else price

        change_amount = price - prev_close
        change_pct = (change_amount / prev_close * 100.0) if prev_close else 0.0

        return RealtimeQuote(
            code=code,
            price=price,
            change_pct=round(change_pct, 4),
            change_amount=round(change_amount, 4),
            volume=volume,
            amount=amount,
            high=high,
            low=low,
            open=open_,
            prev_close=prev_close,
            source=self.name,
        )

    # ─── 日线历史 ────────────────────────────────────────────

    def get_daily_history(self, code: str, start_date: str, end_date: str) -> pd.DataFrame:
        pure_code = code.split(".")[0]
        if detect_asset_type(code) == "etf":
            df = _ak_retry(ak.fund_etf_hist_em)(
                symbol=pure_code, period="daily",
                start_date=start_date, end_date=end_date, adjust="qfq",
            )
        else:
            df = _ak_retry(ak.stock_zh_a_hist)(
                symbol=pure_code, period="daily",
                start_date=start_date, end_date=end_date, adjust="qfq",
            )
        df = df.rename(columns={
            "日期": "trade_date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "换手率": "turnover",
        })
        df["code"] = code
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        return df[["code", "trade_date", "open", "high", "low", "close", "volume", "amount", "turnover"]]

    # ─── 股票列表 ────────────────────────────────────────────

    def get_stock_list(self) -> pd.DataFrame:
        df = _ak_retry(ak.stock_info_a_code_name)()
        df.columns = ["code", "name"]
        return df

    # ─── 基金净值 ────────────────────────────────────────────

    def get_fund_nav(self, code: str) -> pd.DataFrame:
        df = _ak_retry(ak.fund_open_fund_info_em)(symbol=code, indicator="单位净值走势")
        df = df.rename(columns={
            "净值日期": "nav_date", "单位净值": "nav", "日增长率": "daily_return",
        })
        df["code"] = code
        df["nav_date"] = pd.to_datetime(df["nav_date"]).dt.date
        return df

    # ─── 基金基本信息 ────────────────────────────────────────

    def get_fund_basic(self, code: str) -> dict:
        df = _ak_retry(ak.fund_individual_basic_info_xq)(symbol=code)
        info = dict(zip(df["item"], df["value"]))
        return {
            "code": code,
            "name": info.get("基金简称", ""),
            "fund_type": info.get("基金类型", ""),
            "manager": info.get("基金经理", ""),
            "size": info.get("基金规模", ""),
            "setup_date": info.get("成立日期", ""),
        }

    # ─── 大盘指数 ────────────────────────────────────────────

    def get_main_indices(self) -> list[IndexQuote]:
        df = _cached_index_spot_em()
        target = {
            "上证指数": "000001",
            "深证成指": "399001",
            "创业板指": "399006",
            "科创50": "000688",
        }
        result = []
        for _, row in df.iterrows():
            name = row.get("名称", "")
            if name in target:
                result.append(IndexQuote(
                    code=target[name],
                    name=name,
                    price=float(row.get("最新价", 0)),
                    change_pct=float(row.get("涨跌幅", 0)),
                ))
        return result

    # ─── 涨跌统计 ────────────────────────────────────────────

    def get_market_stats(self) -> MarketStats:
        df = _cached_spot_em()
        total = len(df)
        up = len(df[df["涨跌幅"] > 0])
        down = len(df[df["涨跌幅"] < 0])
        flat = total - up - down
        limit_up = len(df[df["涨跌幅"] >= 9.9])
        limit_down = len(df[df["涨跌幅"] <= -9.9])
        return MarketStats(
            total=total, up=up, down=down, flat=flat,
            limit_up=limit_up, limit_down=limit_down,
        )

    # ─── 板块排行 ────────────────────────────────────────────

    def get_sector_rankings(self, top_n: int = 5) -> tuple[list[SectorRank], list[SectorRank]]:
        df = _cached_generic("sector_board", _ak_retry(ak.stock_board_industry_name_em))
        # 按涨跌幅排序
        df = df.sort_values("涨跌幅", ascending=False)
        top_up = []
        for _, r in df.head(top_n).iterrows():
            top_up.append(SectorRank(
                name=str(r.get("板块名称", "")),
                change_pct=float(r.get("涨跌幅", 0)),
                leader=str(r.get("领涨股票", "")),
                leader_pct=float(r.get("领涨股票-涨跌幅", 0)),
            ))
        top_down = []
        for _, r in df.tail(top_n).iterrows():
            top_down.append(SectorRank(
                name=str(r.get("板块名称", "")),
                change_pct=float(r.get("涨跌幅", 0)),
                leader=str(r.get("领涨股票", "")),
                leader_pct=float(r.get("领涨股票-涨跌幅", 0)),
            ))
        return top_up, top_down

    # ─── 两市总成交额 ────────────────────────────────────────

    def get_total_turnover(self) -> float:
        df = _cached_spot_em()
        total_amount = df["成交额"].sum()
        return round(total_amount / 1e8, 2)  # 转亿元

    # ─── 北向资金 ────────────────────────────────────────────

    def get_northbound_flow(self) -> dict:
        df = _cached_generic("northbound", _ak_retry(ak.stock_hsgt_fund_flow_summary_em))
        if df is None or df.empty:
            return {"total": 0, "sh_connect": 0, "sz_connect": 0, "date": ""}
        # 筛选北向资金（沪股通 + 深股通）
        north = df[df["板块"].isin(["沪股通", "深股通"])]
        sh_flow = 0.0
        sz_flow = 0.0
        trade_date = ""
        for _, row in north.iterrows():
            net = float(row.get("成交净买额", 0))
            if row.get("板块") == "沪股通":
                sh_flow = net
            else:
                sz_flow = net
            if not trade_date:
                trade_date = str(row.get("交易日", ""))
        return {
            "total": round(sh_flow + sz_flow, 2),
            "sh_connect": round(sh_flow, 2),
            "sz_connect": round(sz_flow, 2),
            "date": trade_date,
        }

    # ─── 涨跌幅 Top N ────────────────────────────────────────

    def get_top_movers(self, top_n: int = 5) -> tuple[list[TopMover], list[TopMover]]:
        df = _cached_spot_em()
        df = df.sort_values("涨跌幅", ascending=False)
        gainers = []
        for _, r in df.head(top_n).iterrows():
            gainers.append(TopMover(
                code=str(r.get("代码", "")),
                name=str(r.get("名称", "")),
                price=float(r.get("最新价", 0)),
                change_pct=float(r.get("涨跌幅", 0)),
            ))
        losers = []
        for _, r in df.tail(top_n).iterrows():
            losers.append(TopMover(
                code=str(r.get("代码", "")),
                name=str(r.get("名称", "")),
                price=float(r.get("最新价", 0)),
                change_pct=float(r.get("涨跌幅", 0)),
            ))
        return gainers, losers
