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
    TopMover,
    detect_asset_type,
)

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

    # ─── 股票列表 ────────────────────────────────────────────

    def get_stock_list(self) -> pd.DataFrame:
        df = self._pro.stock_basic(
            exchange="", list_status="L",
            fields="ts_code,name,market,industry,list_date",
        )
        df = df.rename(columns={"ts_code": "code"})
        df["list_date"] = pd.to_datetime(df["list_date"]).dt.date
        return df

    # ─── 日线历史 ────────────────────────────────────────────

    def get_daily_history(self, code: str, start_date: str, end_date: str) -> pd.DataFrame:
        if detect_asset_type(code) == "etf":
            df = self._pro.fund_daily(ts_code=code, start_date=start_date, end_date=end_date)
        else:
            df = self._pro.daily(ts_code=code, start_date=start_date, end_date=end_date)
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
        df = self._pro.fina_indicator(
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
        df = self._pro.daily_basic(**kwargs)
        if df.empty:
            return df
        df = df.rename(columns={"ts_code": "code"})
        return df

    # ─── 实时行情（用最近交易日收盘近似）────────────────────

    def _latest_trade_date(self) -> str:
        """获取最近一个交易日"""
        today = datetime.now().strftime("%Y%m%d")
        df = self._pro.trade_cal(
            exchange="", start_date=(datetime.now() - timedelta(days=10)).strftime("%Y%m%d"),
            end_date=today, is_open="1",
        )
        if df.empty:
            return today
        return df.sort_values("cal_date", ascending=False).iloc[0]["cal_date"]

    def get_realtime_quote(self, code: str) -> RealtimeQuote:
        trade_date = self._latest_trade_date()
        if detect_asset_type(code) == "etf":
            df = self._pro.fund_daily(ts_code=code, start_date=trade_date, end_date=trade_date)
        else:
            df = self._pro.daily(ts_code=code, start_date=trade_date, end_date=trade_date)
        if df.empty:
            raise ValueError(f"Tushare 未找到 {code} 在 {trade_date} 的数据")
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
        )

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
                df = self._pro.index_daily(ts_code=ts_code, start_date=trade_date, end_date=trade_date)
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
        df = self._pro.daily(trade_date=trade_date)
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
        df = self._pro.daily(trade_date=trade_date)
        if df.empty:
            return 0.0
        # Tushare amount 单位是千元
        total = df["amount"].sum()
        return round(total / 1e5, 2)  # 千元→亿元

    # ─── 北向资金 ────────────────────────────────────────────

    def get_northbound_flow(self) -> dict:
        trade_date = self._latest_trade_date()
        start = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
        df = self._pro.moneyflow_hsgt(start_date=start, end_date=trade_date)
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

    # ─── 涨跌幅 Top N ────────────────────────────────────────

    def get_top_movers(self, top_n: int = 5) -> tuple[list[TopMover], list[TopMover]]:
        trade_date = self._latest_trade_date()
        df = self._pro.daily(trade_date=trade_date)
        if df.empty:
            return [], []
        # 获取股票名称
        basics = self._pro.stock_basic(exchange="", list_status="L", fields="ts_code,name")
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
