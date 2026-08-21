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
    DataFetchError,
    DataProvider,
    IndexQuote,
    MarketStats,
    RealtimeQuote,
    SectorRank,
    TopMover,
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
# get_total_turnover + get_top_movers，三者各自拉取全 A 股快照。
# 短缓存让它们共享一次拉取，盘中 30s 内不重复请求同一重数据。
# 东财(*_em)接口易被封禁已弃用，实时快照统一走新浪。

_CACHE_TTL = float(os.environ.get("XSHARE_AKSHARE_SPOT_TTL", "30") or "30")
_spot_cache_lock = threading.Lock()
_spot_cache: dict[str, tuple[float, pd.DataFrame]] = {}


def _sina_to_std_code(sina_code: str) -> str:
    """新浪代码 (sh600519 / sz002594 / bj430047) → 标准代码 (600519.SH)。"""
    sina_code = str(sina_code).strip().lower()
    if len(sina_code) > 2 and sina_code[:2] in ("sh", "sz", "bj"):
        return f"{sina_code[2:]}.{sina_code[:2].upper()}"
    return sina_code


def _fetch_spot_sina() -> pd.DataFrame:
    """新浪全 A 股实时快照（无缓存），归一化为英文列。

    返回列：code, name, price, change_pct, change_amount, open, high, low,
    prev_close, volume, amount。新浪源无 turnover/pe/pb/total_mv。
    """
    df = _ak_retry(ak.stock_zh_a_spot)()
    if df is None or df.empty:
        return df
    out = pd.DataFrame({
        "code": df["代码"].map(_sina_to_std_code),
        "name": df["名称"].astype(str),
        "price": pd.to_numeric(df["最新价"], errors="coerce"),
        "change_amount": pd.to_numeric(df["涨跌额"], errors="coerce"),
        "change_pct": pd.to_numeric(df["涨跌幅"], errors="coerce"),
        "prev_close": pd.to_numeric(df["昨收"], errors="coerce"),
        "open": pd.to_numeric(df["今开"], errors="coerce"),
        "high": pd.to_numeric(df["最高"], errors="coerce"),
        "low": pd.to_numeric(df["最低"], errors="coerce"),
        "volume": pd.to_numeric(df["成交量"], errors="coerce"),
        "amount": pd.to_numeric(df["成交额"], errors="coerce"),
    })
    # 停牌/无成交时最新价为 0，无法参与统计
    return out[out["price"] > 0].reset_index(drop=True)


def _fetch_index_spot_sina() -> pd.DataFrame:
    """新浪指数实时快照（无缓存）。返回列：code, name, price, change_pct。"""
    df = _ak_retry(ak.stock_zh_index_spot_sina)()
    if df is None or df.empty:
        return df
    return pd.DataFrame({
        "code": df["代码"].map(_sina_to_std_code),
        "name": df["名称"].astype(str),
        "price": pd.to_numeric(df["最新价"], errors="coerce"),
        "change_pct": pd.to_numeric(df["涨跌幅"], errors="coerce"),
    })


def _fetch_sector_spot_sina() -> pd.DataFrame:
    """新浪行业板块快照（无缓存），自带领涨股。

    返回列：name, change_pct, leader, leader_pct。
    """
    df = _ak_retry(ak.stock_sector_spot)(indicator="新浪行业")
    if df is None or df.empty:
        return df
    return pd.DataFrame({
        "name": df["板块"].astype(str),
        "change_pct": pd.to_numeric(df["涨跌幅"], errors="coerce"),
        "leader": df["股票名称"].astype(str),
        "leader_pct": pd.to_numeric(df["个股-涨跌幅"], errors="coerce"),
    })


def _cached_spot() -> pd.DataFrame:
    """带 TTL 的全 A 股快照缓存（新浪源）。"""
    now = time.time()
    with _spot_cache_lock:
        entry = _spot_cache.get("spot")
        if entry and (now - entry[0]) < _CACHE_TTL:
            logger.debug("复用新浪全 A 快照缓存（age=%.1fs）", now - entry[0])
            return entry[1].copy()
    df = _fetch_spot_sina()
    if df is None or df.empty:
        return df
    with _spot_cache_lock:
        _spot_cache["spot"] = (time.time(), df)
    return df.copy()


def _cached_generic(key: str, fetch: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    """带 TTL 的通用快照缓存，供指数/板块等独立 HTTP 接口复用。

    与 _cached_spot 同样的锁/过期机制，避免每次大盘概览都裸打 akshare。
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
        # akshare 侧日线历史只有东财源（stock_zh_a_hist / fund_etf_hist_em），
        # 易封禁已停用；抛错让 failover 落到 Tushare。
        raise DataFetchError("akshare 日线历史(东财)已停用，请使用 Tushare")

    # ─── 股票列表 ────────────────────────────────────────────

    def get_stock_list(self) -> pd.DataFrame:
        df = _ak_retry(ak.stock_info_a_code_name)()
        df.columns = ["code", "name"]
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
        df = _cached_generic("index", _fetch_index_spot_sina)
        target = {
            "上证指数": "000001",
            "深证成指": "399001",
            "创业板指": "399006",
            "科创50": "000688",
        }
        result = []
        for _, row in df.iterrows():
            name = row.get("name", "")
            if name in target:
                result.append(IndexQuote(
                    code=target[name],
                    name=name,
                    price=float(row.get("price", 0)),
                    change_pct=float(row.get("change_pct", 0)),
                ))
        return result

    # ─── 涨跌统计 ────────────────────────────────────────────

    def get_market_stats(self) -> MarketStats:
        df = _cached_spot()
        total = len(df)
        up = len(df[df["change_pct"] > 0])
        down = len(df[df["change_pct"] < 0])
        flat = total - up - down
        limit_up = len(df[df["change_pct"] >= 9.9])
        limit_down = len(df[df["change_pct"] <= -9.9])
        return MarketStats(
            total=total, up=up, down=down, flat=flat,
            limit_up=limit_up, limit_down=limit_down,
        )

    # ─── 板块排行 ────────────────────────────────────────────

    def get_sector_rankings(self, top_n: int = 5) -> tuple[list[SectorRank], list[SectorRank]]:
        df = _cached_generic("sector_board", _fetch_sector_spot_sina)
        df = df.sort_values("change_pct", ascending=False)
        top_up = []
        for _, r in df.head(top_n).iterrows():
            top_up.append(SectorRank(
                name=str(r.get("name", "")),
                change_pct=float(r.get("change_pct", 0)),
                leader=str(r.get("leader", "")),
                leader_pct=float(r.get("leader_pct", 0)),
            ))
        top_down = []
        for _, r in df.tail(top_n).iterrows():
            top_down.append(SectorRank(
                name=str(r.get("name", "")),
                change_pct=float(r.get("change_pct", 0)),
                leader=str(r.get("leader", "")),
                leader_pct=float(r.get("leader_pct", 0)),
            ))
        return top_up, top_down

    # ─── 两市总成交额 ────────────────────────────────────────

    def get_total_turnover(self) -> float:
        df = _cached_spot()
        total_amount = df["amount"].sum()
        return round(total_amount / 1e8, 2)  # 转亿元

    # ─── 北向资金 ────────────────────────────────────────────

    def get_northbound_flow(self) -> dict:
        # 东财接口已停用；北向资金改由 Tushare moneyflow_hsgt 提供（见 ProviderManager）。
        raise NotImplementedError

    # ─── 涨跌幅 Top N ────────────────────────────────────────

    def get_top_movers(self, top_n: int = 5) -> tuple[list[TopMover], list[TopMover]]:
        df = _cached_spot()
        df = df.sort_values("change_pct", ascending=False)
        gainers = []
        for _, r in df.head(top_n).iterrows():
            gainers.append(TopMover(
                code=str(r.get("code", "")),
                name=str(r.get("name", "")),
                price=float(r.get("price", 0)),
                change_pct=float(r.get("change_pct", 0)),
            ))
        losers = []
        for _, r in df.tail(top_n).iterrows():
            losers.append(TopMover(
                code=str(r.get("code", "")),
                name=str(r.get("name", "")),
                price=float(r.get("price", 0)),
                change_pct=float(r.get("change_pct", 0)),
            ))
        return gainers, losers
