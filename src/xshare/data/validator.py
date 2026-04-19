"""数据校验"""


def validate_price(price: float) -> bool:
    """价格校验：必须 > 0"""
    return price is not None and price > 0


def validate_change_pct(change_pct: float, is_st: bool = False, is_new: bool = False) -> str | None:
    """涨跌幅校验，返回异常标记或 None"""
    limit = 20.0 if (is_st or is_new) else 10.5
    if abs(change_pct) > limit:
        return f"涨跌幅异常: {change_pct}%（限制 {limit}%）"
    return None


def filter_invalid_rows(df, price_col: str = "close"):
    """过滤无效行（价格 <= 0）"""
    return df[df[price_col] > 0].copy()
