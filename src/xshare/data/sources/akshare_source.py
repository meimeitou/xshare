"""数据源适配层 - AKShare"""

import akshare as ak
import pandas as pd


def fetch_stock_list() -> pd.DataFrame:
    """获取A股股票列表"""
    df = ak.stock_info_a_code_name()
    df.columns = ["code", "name"]
    return df


def fetch_realtime_quote(code: str) -> dict:
    """获取实时行情（AKShare 东财接口）"""
    # code 格式转换：002594.SZ -> sz002594
    market = code.split(".")[-1].lower()
    pure_code = code.split(".")[0]
    symbol = f"{market}{pure_code}"

    df = ak.stock_zh_a_spot_em()
    row = df[df["代码"] == pure_code]
    if row.empty:
        raise ValueError(f"未找到股票: {code}")

    r = row.iloc[0]
    return {
        "code": code,
        "name": str(r.get("名称", "")),
        "price": float(r.get("最新价", 0)),
        "change_pct": float(r.get("涨跌幅", 0)),
        "change_amount": float(r.get("涨跌额", 0)),
        "volume": int(r.get("成交量", 0)),
        "amount": float(r.get("成交额", 0)),
        "high": float(r.get("最高", 0)),
        "low": float(r.get("最低", 0)),
        "open": float(r.get("今开", 0)),
        "prev_close": float(r.get("昨收", 0)),
        "turnover": float(r.get("换手率", 0)),
    }


def fetch_daily_history(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """获取日线历史数据"""
    pure_code = code.split(".")[0]
    df = ak.stock_zh_a_hist(symbol=pure_code, period="daily",
                            start_date=start_date, end_date=end_date, adjust="qfq")
    df = df.rename(columns={
        "日期": "trade_date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "换手率": "turnover",
    })
    df["code"] = code
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df[["code", "trade_date", "open", "high", "low", "close", "volume", "amount", "turnover"]]


def fetch_fund_nav(code: str) -> pd.DataFrame:
    """获取基金净值历史"""
    df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
    df = df.rename(columns={"净值日期": "nav_date", "单位净值": "nav", "日增长率": "daily_return"})
    df["code"] = code
    df["nav_date"] = pd.to_datetime(df["nav_date"]).dt.date
    return df


def fetch_fund_basic(code: str) -> dict:
    """获取基金基本信息"""
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
