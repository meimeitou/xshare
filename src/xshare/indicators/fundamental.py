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
