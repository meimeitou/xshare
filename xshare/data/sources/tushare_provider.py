"""Tushare Pro 数据源 Provider"""

import logging
import os
from datetime import datetime, timedelta

import pandas as pd
import tushare as ts

from xshare.data.provider import (
    DataProvider,
    IndexQuote,
    MarketStats,
    RealtimeQuote,
    SectorRank,
    TopMover,
    detect_asset_type,
)
from xshare.data.sources.tushare_client import TushareClient

logger = logging.getLogger(__name__)


class TushareProvider(DataProvider):
    name = "tushare"
    priority = 0  # 有 token 时优先级最高

    def __init__(self):
        self.priority = int(os.getenv("TUSHARE_PRIORITY", "0"))
        token = os.environ.get("TUSHARE_TOKEN", "")
        if not token:
            raise RuntimeError("TUSHARE_TOKEN 未设置")
        self._pro = ts.pro_api(token)

    @property
    def _client(self) -> TushareClient:
        """懒加载 TushareClient，包装 ``self._pro``。

        测试可通过 ``provider._pro = FakePro()`` 注入桩件，首次访问
        ``_client`` 时自动创建包装该 pro 的客户端。
        """
        if "_client_obj" not in self.__dict__:
            self.__dict__["_client_obj"] = TushareClient(
                lambda method, kwargs: getattr(self._pro, method)(**kwargs)
            )
        return self.__dict__["_client_obj"]

    # ─── 股票列表 ────────────────────────────────────────────

    def get_stock_list(self) -> pd.DataFrame:
        df = self._client.call("stock_basic",
            exchange="", list_status="L",
            fields="ts_code,name,market,industry,list_date",
        )
        df = df.rename(columns={"ts_code": "code"})
        df["list_date"] = pd.to_datetime(df["list_date"]).dt.date
        return df

    # ─── 日线历史 ────────────────────────────────────────────

    def get_daily_history(self, code: str, start_date: str, end_date: str) -> pd.DataFrame:
        if detect_asset_type(code) == "etf":
            df = self._client.call("fund_daily", ts_code=code, start_date=start_date, end_date=end_date)
        else:
            df = self._client.call("daily", ts_code=code, start_date=start_date, end_date=end_date)
        if df.empty:
            return df
        df = df.rename(columns={
            "ts_code": "code", "trade_date": "trade_date",
            "vol": "volume", "amount": "amount",
        })
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        return df[["code", "trade_date", "open", "high", "low", "close", "volume", "amount"]]
    # ─── 财务指标 ────────────────────────────────────────────

    def get_financial_data(self, code: str) -> pd.DataFrame:
        df = self._client.call("fina_indicator",
            ts_code=code,
            fields="ts_code,end_date,roe,revenue_ps,profit_to_gr,q_roe",
        )
        if df.empty:
            return df
        df = df.rename(columns={"ts_code": "code"})
        df["end_date"] = pd.to_datetime(df["end_date"]).dt.date
        return df

    # ─── 每日指标 ────────────────────────────────────────────

    def get_daily_basic(self, code: str, trade_date: str = "") -> pd.DataFrame:
        kwargs = {
            "ts_code": code,
            "fields": "ts_code,trade_date,pe,pb,ps,total_mv,circ_mv,turnover_rate",
        }
        if trade_date:
            kwargs["trade_date"] = trade_date
        df = self._client.call("daily_basic", **kwargs)
        if df.empty:
            return df
        df = df.rename(columns={"ts_code": "code"})
        return df

    # ─── 实时行情（用最近交易日收盘近似）────────────────────

    def _latest_trade_date(self) -> str:
        """获取最近一个交易日 (优先从缓存查询，然后从 API)"""
        from xshare.data.db import get_conn
        
        today = datetime.now().strftime("%Y%m%d")
        
        # 1. 优先从 DuckDB 缓存查询最近交易日
        try:
            conn = get_conn()
            df = conn.execute(
                "SELECT MAX(trade_date) as latest FROM stock_daily"
            ).fetchdf()
            if not df.empty and df.iloc[0, 0] is not None:
                latest_value = df.iloc[0, 0]
                latest = str(latest_value).split()[0].replace("-", "")
                logger.debug("从缓存获取最近交易日: %s", latest)
                return latest
        except Exception as e:
            logger.debug("从缓存查询最近交易日失败: %s", e)
        
        # 2. 缓存失败，尝试从 API 查询大盘数据
        start_date = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
        try:
            # 直接查询大盘指数的最近数据，获取最新的交易日
            df = self._client.call("daily", ts_code="000001.SH", start_date=start_date, end_date=today)
            if not df.empty:
                latest = df.sort_values("trade_date", ascending=False).iloc[0]["trade_date"]
                logger.debug("从 API 获取最近交易日: %s", latest)
                return latest
        except Exception as e:
            logger.debug("查询大盘数据失败: %s", e)
        
        # 3. 大盘查询失败，降级回交易日历
        try:
            df = self._client.call("trade_cal",
                exchange="",
                start_date=(datetime.now() - timedelta(days=30)).strftime("%Y%m%d"),
                end_date=today,
                is_open="1",  # 只查询交易日
            )
            if not df.empty:
                latest = df.sort_values("cal_date", ascending=False).iloc[0]["cal_date"]
                logger.debug("从交易日历获取最近交易日: %s", latest)
                return latest
        except Exception as e:
            logger.debug("查询交易日历失败: %s", e)
        
        # 都失败了，返回当前日期
        logger.warning("无法查询最近交易日，返回当前日期 %s", today)
        return today

    def _recent_trade_dates(self, lookback_days: int = 30, max_dates: int = 10) -> list[str]:
        """获取最近一段时间内的交易日（按新到旧）。"""
        today = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")

        try:
            cal = self._client.call("trade_cal",
                exchange="",
                start_date=start_date,
                end_date=today,
                is_open="1",
            )
            if cal.empty:
                latest = self._latest_trade_date()
                return [latest] if latest else []
            dates = cal.sort_values("cal_date", ascending=False)["cal_date"].astype(str).tolist()
            return dates[:max_dates]
        except Exception:
            latest = self._latest_trade_date()
            return [latest] if latest else []

    def get_realtime_quote(self, code: str) -> RealtimeQuote:
        tried_dates: list[str] = []
        for trade_date in self._recent_trade_dates(lookback_days=30, max_dates=10):
            tried_dates.append(trade_date)
            if detect_asset_type(code) == "etf":
                df = self._client.call("fund_daily", ts_code=code, start_date=trade_date, end_date=trade_date)
            else:
                df = self._client.call("daily", ts_code=code, start_date=trade_date, end_date=trade_date)

            if df.empty:
                continue

            r = df.iloc[0]
            return RealtimeQuote(
                code=code,
                price=float(r.get("close", 0)),
                change_pct=float(r.get("pct_chg", 0)),
                change_amount=float(r.get("change", 0)),
                volume=int(r.get("vol", 0)),
                amount=float(r.get("amount", 0)),
                high=float(r.get("high", 0)),
                low=float(r.get("low", 0)),
                open=float(r.get("open", 0)),
                prev_close=float(r.get("pre_close", 0)),
                source=self.name,
                as_of=trade_date,
                is_delayed=True,  # Tushare 无实时权限，此为日线收盘近似
            )

        tried = ",".join(tried_dates) if tried_dates else "<none>"
        raise ValueError(f"Tushare 未找到 {code} 在最近交易日的数据（已尝试: {tried}）")

    # ─── 主要指数 ────────────────────────────────────────────

    def get_main_indices(self) -> list[IndexQuote]:
        trade_date = self._latest_trade_date()
        indices = {
            "000001.SH": "上证指数",
            "399001.SZ": "深证成指",
            "399006.SZ": "创业板指",
            "000688.SH": "科创50",
        }
        result = []
        for ts_code, name in indices.items():
            try:
                df = self._client.call("index_daily", ts_code=ts_code, start_date=trade_date, end_date=trade_date)
                if not df.empty:
                    r = df.iloc[0]
                    result.append(IndexQuote(
                        code=ts_code,
                        name=name,
                        price=float(r.get("close", 0)),
                        change_pct=float(r.get("pct_chg", 0)),
                    ))
            except Exception as e:
                logger.warning("Tushare index_daily %s failed: %s", ts_code, e)
        return result

    # ─── 涨跌统计 ────────────────────────────────────────────

    def get_market_stats(self) -> MarketStats:
        trade_date = self._latest_trade_date()
        df = self._client.call("daily", trade_date=trade_date)
        if df.empty:
            return MarketStats()
        total = len(df)
        up = len(df[df["pct_chg"] > 0])
        down = len(df[df["pct_chg"] < 0])
        flat = total - up - down
        limit_up = len(df[df["pct_chg"] >= 9.9])
        limit_down = len(df[df["pct_chg"] <= -9.9])
        return MarketStats(
            total=total, up=up, down=down, flat=flat,
            limit_up=limit_up, limit_down=limit_down,
        )

    # ─── 两市总成交额 ────────────────────────────────────────

    def get_total_turnover(self) -> float:
        trade_date = self._latest_trade_date()
        df = self._client.call("daily", trade_date=trade_date)
        if df.empty:
            return 0.0
        # Tushare amount 单位是千元
        total = df["amount"].sum()
        return round(total / 1e5, 2)  # 千元→亿元

    # ─── 北向资金 ────────────────────────────────────────────

    def get_northbound_flow(self) -> dict:
        trade_date = self._latest_trade_date()
        start = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
        df = self._client.call("moneyflow_hsgt", start_date=start, end_date=trade_date)
        if df.empty:
            return {"total": 0, "sh_connect": 0, "sz_connect": 0, "date": ""}
        latest = df.sort_values("trade_date", ascending=False).iloc[0]
        # north_money 单位百万
        north = float(latest.get("north_money", 0))
        hgt = float(latest.get("hgt", 0))
        sgt = float(latest.get("sgt", 0))
        return {
            "total": round(north / 100, 2),  # 百万→亿
            "sh_connect": round(hgt / 100, 2),
            "sz_connect": round(sgt / 100, 2),
            "date": str(latest.get("trade_date", "")),
        }

    # ─── 板块排行（行业聚合）────────────────────────────────────

    def get_sector_rankings(self, top_n: int = 5) -> tuple[list[SectorRank], list[SectorRank]]:
        """基于当日个股涨跌幅聚合行业排行，返回(涨幅前N, 跌幅前N)"""
        trade_date = self._latest_trade_date()
        daily = self._client.call("daily", trade_date=trade_date)
        if daily.empty:
            return [], []

        basics = self._client.call("stock_basic", exchange="", list_status="L", fields="ts_code,name,industry")
        if basics.empty:
            return [], []

        merged = daily.merge(basics, on="ts_code", how="left")
        merged = merged.dropna(subset=["industry", "pct_chg"])
        if merged.empty:
            return [], []

        rows = []
        for industry, group in merged.groupby("industry"):
            if group.empty:
                continue
            avg_change = float(group["pct_chg"].mean())
            leader_row = group.loc[group["pct_chg"].idxmax()]
            rows.append(
                {
                    "industry": str(industry),
                    "change_pct": avg_change,
                    "leader": str(leader_row.get("name", "")),
                    "leader_pct": float(leader_row.get("pct_chg", 0)),
                }
            )

        if not rows:
            return [], []

        df_rank = pd.DataFrame(rows).sort_values("change_pct", ascending=False)

        top_up = [
            SectorRank(
                name=str(r["industry"]),
                change_pct=float(r["change_pct"]),
                leader=str(r["leader"]),
                leader_pct=float(r["leader_pct"]),
            )
            for _, r in df_rank.head(top_n).iterrows()
        ]
        top_down = [
            SectorRank(
                name=str(r["industry"]),
                change_pct=float(r["change_pct"]),
                leader=str(r["leader"]),
                leader_pct=float(r["leader_pct"]),
            )
            for _, r in df_rank.tail(top_n).iterrows()
        ]
        return top_up, top_down

    # ─── 涨跌幅 Top N ────────────────────────────────────────

    def get_top_movers(self, top_n: int = 5) -> tuple[list[TopMover], list[TopMover]]:
        trade_date = self._latest_trade_date()
        df = self._client.call("daily", trade_date=trade_date)
        if df.empty:
            return [], []
        # 获取股票名称
        basics = self._client.call("stock_basic", exchange="", list_status="L", fields="ts_code,name")
        name_map = dict(zip(basics["ts_code"], basics["name"])) if not basics.empty else {}

        df = df.sort_values("pct_chg", ascending=False)
        gainers = []
        for _, r in df.head(top_n).iterrows():
            code = r.get("ts_code", "")
            gainers.append(TopMover(
                code=code,
                name=name_map.get(code, ""),
                price=float(r.get("close", 0)),
                change_pct=float(r.get("pct_chg", 0)),
            ))
        losers = []
        for _, r in df.tail(top_n).iterrows():
            code = r.get("ts_code", "")
            losers.append(TopMover(
                code=code,
                name=name_map.get(code, ""),
                price=float(r.get("close", 0)),
                change_pct=float(r.get("pct_chg", 0)),
            ))
        return gainers, losers
