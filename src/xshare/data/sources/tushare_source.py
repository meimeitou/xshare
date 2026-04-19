"""数据源适配层 - Tushare"""

import os

import pandas as pd
import tushare as ts

_pro: ts.pro_api | None = None


def _get_pro() -> ts.pro_api:
    """获取 Tushare Pro API"""
    global _pro
    if _pro is None:
        token = os.environ.get("TUSHARE_TOKEN", "")
        if not token:
            raise RuntimeError("请设置环境变量 TUSHARE_TOKEN")
        _pro = ts.pro_api(token)
    return _pro


def fetch_stock_basic() -> pd.DataFrame:
    """获取A股股票基本信息"""
    pro = _get_pro()
    df = pro.stock_basic(exchange="", list_status="L",
                         fields="ts_code,name,market,industry,list_date")
    df = df.rename(columns={"ts_code": "code"})
    df["list_date"] = pd.to_datetime(df["list_date"]).dt.date
    return df


def fetch_daily(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """获取日线行情"""
    pro = _get_pro()
    df = pro.daily(ts_code=code, start_date=start_date, end_date=end_date)
    df = df.rename(columns={
        "ts_code": "code", "trade_date": "trade_date",
        "vol": "volume", "amount": "amount",
    })
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df[["code", "trade_date", "open", "high", "low", "close", "volume", "amount"]]


def fetch_financial_indicators(code: str) -> pd.DataFrame:
    """获取财务指标"""
    pro = _get_pro()
    df = pro.fina_indicator(ts_code=code,
                            fields="ts_code,end_date,roe,revenue_ps,profit_to_gr,q_roe")
    df = df.rename(columns={"ts_code": "code"})
    df["end_date"] = pd.to_datetime(df["end_date"]).dt.date
    return df


def fetch_daily_basic(code: str, trade_date: str = "") -> pd.DataFrame:
    """获取每日指标（PE/PB/换手率等）"""
    pro = _get_pro()
    kwargs = {"ts_code": code, "fields": "ts_code,trade_date,pe,pb,ps,total_mv,circ_mv,turnover_rate"}
    if trade_date:
        kwargs["trade_date"] = trade_date
    df = pro.daily_basic(**kwargs)
    df = df.rename(columns={"ts_code": "code"})
    return df
