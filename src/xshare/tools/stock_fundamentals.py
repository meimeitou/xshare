"""基本面数据"""

import json

import pandas as pd

from xshare.data.provider import get_provider, DataFetchError
from xshare.indicators.fundamental import (
    calc_roe_trend,
    calc_revenue_growth,
    calc_pe_percentile,
    calc_peg,
    calc_profit_margins,
    calc_revenue_trend,
    calc_profit_trend,
)


async def stock_fundamentals(args: dict) -> str:
    """获取股票基本面数据（含派生指标）"""
    code = args["code"]

    try:
        df = get_provider().get_financial_data(code)
    except DataFetchError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    if df.empty:
        return json.dumps({"error": f"未找到 {code} 的财务数据"}, ensure_ascii=False)

    df_sorted = df.sort_values("end_date", ascending=False)

    # 最新一期原始数据
    row = df_sorted.iloc[0]
    data = row.to_dict()
    for k, v in data.items():
        if hasattr(v, "isoformat"):
            data[k] = v.isoformat()

    # ── 派生指标 ──

    # PEG
    pe = float(row["pe"]) if pd.notna(row.get("pe")) else None
    profit_yoy = float(row["profit_yoy"]) if pd.notna(row.get("profit_yoy")) else None
    peg = calc_peg(pe, profit_yoy)
    if peg is not None:
        data["peg"] = peg

    # PE 历史分位
    if pe is not None and "pe" in df_sorted.columns:
        historical_pe = df_sorted["pe"].dropna().astype(float)
        percentile = calc_pe_percentile(pe, historical_pe)
        if percentile is not None:
            data["pe_percentile"] = percentile

    # ROE 趋势（近 8 个季度）
    roe_trend = calc_roe_trend(df_sorted.head(8))
    if roe_trend:
        data["roe_trend"] = roe_trend

    # 营收同比
    rev_growth = calc_revenue_growth(df_sorted)
    if rev_growth is not None:
        data["revenue_growth"] = rev_growth

    # 净利率趋势
    margin_data = calc_profit_margins(df_sorted.head(8))
    if margin_data:
        data.update(margin_data)

    # 营收趋势
    rev_trend = calc_revenue_trend(df_sorted.head(8))
    if rev_trend:
        data["revenue_trend"] = rev_trend

    # 净利润趋势
    profit_trend = calc_profit_trend(df_sorted.head(8))
    if profit_trend:
        data["profit_trend"] = profit_trend

    return json.dumps(data, ensure_ascii=False)
