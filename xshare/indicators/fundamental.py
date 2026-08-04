"""基本面指标计算"""

import pandas as pd


def calc_roe_trend(df: pd.DataFrame) -> list[dict]:
    """ROE 趋势（近 N 个季度）"""
    if "roe" not in df.columns or df.empty:
        return []
    return [
        {"date": str(row["end_date"]), "roe": round(row["roe"], 2)}
        for _, row in df.iterrows()
        if pd.notna(row["roe"])
    ]


def calc_revenue_growth(df: pd.DataFrame) -> float | None:
    """营收同比增速（最新季度）"""
    if "revenue_yoy" not in df.columns or df.empty:
        return None
    latest = df.iloc[0]
    return round(float(latest["revenue_yoy"]), 2) if pd.notna(latest["revenue_yoy"]) else None


def calc_pe_percentile(pe: float, historical_pe: pd.Series) -> float | None:
    """PE 历史分位"""
    if historical_pe.empty or pe is None:
        return None
    return round((historical_pe < pe).mean() * 100, 2)


def calc_peg(pe: float | None, profit_yoy: float | None) -> float | None:
    """PEG = PE / 盈利增速（%）"""
    if pe is None or profit_yoy is None or profit_yoy == 0:
        return None
    return round(pe / profit_yoy, 2)


def calc_profit_margins(df: pd.DataFrame) -> dict:
    """净利率（近 N 个季度），需要 revenue 和 net_profit"""
    if df.empty or "revenue" not in df.columns or "net_profit" not in df.columns:
        return {}
    result = []
    for _, row in df.iterrows():
        if pd.notna(row["revenue"]) and row["revenue"] > 0 and pd.notna(row["net_profit"]):
            margin = round(row["net_profit"] / row["revenue"] * 100, 2)
            result.append({"date": str(row["end_date"]), "net_margin_pct": margin})
    return {"net_margin_trend": result} if result else {}


def calc_revenue_trend(df: pd.DataFrame) -> list[dict]:
    """营收趋势（近 N 个季度）"""
    if df.empty or "revenue" not in df.columns:
        return []
    return [
        {"date": str(row["end_date"]), "revenue": round(row["revenue"], 2)}
        for _, row in df.iterrows()
        if pd.notna(row["revenue"])
    ]


def calc_profit_trend(df: pd.DataFrame) -> list[dict]:
    """净利润趋势（近 N 个季度）"""
    if df.empty or "net_profit" not in df.columns:
        return []
    return [
        {"date": str(row["end_date"]), "net_profit": round(row["net_profit"], 2)}
        for _, row in df.iterrows()
        if pd.notna(row["net_profit"])
    ]
