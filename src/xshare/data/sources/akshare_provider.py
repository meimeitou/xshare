"""AkShare 数据源 Provider"""

import logging

import akshare as ak
import pandas as pd

from xshare.data.provider import (
    DataProvider,
    IndexQuote,
    MarketStats,
    RealtimeQuote,
    SectorRank,
    TopMover,
)

logger = logging.getLogger(__name__)


class AkShareProvider(DataProvider):
    name = "akshare"
    priority = 1  # 默认优先级，可通过环境变量覆盖

    def __init__(self):
        import os
        self.priority = int(os.getenv("AKSHARE_PRIORITY", "1"))

    # ─── 实时行情 ────────────────────────────────────────────

    def get_realtime_quote(self, code: str) -> RealtimeQuote:
        pure_code = code.split(".")[0]
        df = ak.stock_zh_a_spot_em()
        row = df[df["代码"] == pure_code]
        if row.empty:
            raise ValueError(f"未找到股票: {code}")

        r = row.iloc[0]
        return RealtimeQuote(
            code=code,
            name=str(r.get("名称", "")),
            price=float(r.get("最新价", 0)),
            change_pct=float(r.get("涨跌幅", 0)),
            change_amount=float(r.get("涨跌额", 0)),
            volume=int(r.get("成交量", 0)),
            amount=float(r.get("成交额", 0)),
            high=float(r.get("最高", 0)),
            low=float(r.get("最低", 0)),
            open=float(r.get("今开", 0)),
            prev_close=float(r.get("昨收", 0)),
            turnover=float(r.get("换手率", 0)),
            source=self.name,
        )

    # ─── 日线历史 ────────────────────────────────────────────

    def get_daily_history(self, code: str, start_date: str, end_date: str) -> pd.DataFrame:
        pure_code = code.split(".")[0]
        df = ak.stock_zh_a_hist(
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
        df = ak.stock_info_a_code_name()
        df.columns = ["code", "name"]
        return df

    # ─── 基金净值 ────────────────────────────────────────────

    def get_fund_nav(self, code: str) -> pd.DataFrame:
        df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        df = df.rename(columns={
            "净值日期": "nav_date", "单位净值": "nav", "日增长率": "daily_return",
        })
        df["code"] = code
        df["nav_date"] = pd.to_datetime(df["nav_date"]).dt.date
        return df

    # ─── 基金基本信息 ────────────────────────────────────────

    def get_fund_basic(self, code: str) -> dict:
        df = ak.fund_individual_basic_info_xq(symbol=code)
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
        df = ak.stock_zh_index_spot_em()
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
        df = ak.stock_zh_a_spot_em()
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
        df = ak.stock_board_industry_name_em()
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
        df = ak.stock_zh_a_spot_em()
        total_amount = df["成交额"].sum()
        return round(total_amount / 1e8, 2)  # 转亿元

    # ─── 北向资金 ────────────────────────────────────────────

    def get_northbound_flow(self) -> dict:
        df = ak.stock_hsgt_fund_flow_summary_em()
        if df.empty:
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
        df = ak.stock_zh_a_spot_em()
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
