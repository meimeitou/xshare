"""技术指标计算 - 纯 pandas 实现"""

import pandas as pd


def calculate_indicators(df: pd.DataFrame, indicators: list[str]) -> dict:
    """根据指定的指标列表计算技术指标"""
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    result = {}

    for ind in indicators:
        ind_upper = ind.upper()
        if ind_upper == "MA":
            result["ma"] = _calc_ma(close)
        elif ind_upper == "EMA":
            result["ema"] = _calc_ema(close)
        elif ind_upper == "MACD":
            result["macd"] = _calc_macd(close)
        elif ind_upper == "RSI":
            result["rsi"] = _calc_rsi(close)
        elif ind_upper == "KDJ":
            result["kdj"] = _calc_kdj(high, low, close)
        elif ind_upper == "BOLL":
            result["boll"] = _calc_boll(close)
        elif ind_upper == "ATR":
            result["atr"] = _calc_atr(high, low, close)

    return result


def _calc_ma(close: pd.Series) -> dict:
    """均线"""
    latest = {}
    for period in [5, 10, 20, 60]:
        ma = close.rolling(period).mean()
        latest[f"ma{period}"] = round(ma.iloc[-1], 2) if len(ma) >= period else None
    return latest


def _calc_ema(close: pd.Series) -> dict:
    """指数均线"""
    latest = {}
    for period in [12, 26]:
        ema = close.ewm(span=period, adjust=False).mean()
        latest[f"ema{period}"] = round(ema.iloc[-1], 2)
    return latest


def _calc_macd(close: pd.Series) -> dict:
    """MACD"""
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_bar = (dif - dea) * 2
    return {
        "dif": round(dif.iloc[-1], 4),
        "dea": round(dea.iloc[-1], 4),
        "macd": round(macd_bar.iloc[-1], 4),
    }


def _calc_rsi(close: pd.Series, period: int = 14) -> dict:
    """RSI"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return {"rsi": round(rsi.iloc[-1], 2), "period": period}


def _calc_kdj(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9) -> dict:
    """KDJ"""
    low_n = low.rolling(n).min()
    high_n = high.rolling(n).max()
    rsv = (close - low_n) / (high_n - low_n) * 100

    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3 * k - 2 * d
    return {
        "k": round(k.iloc[-1], 2),
        "d": round(d.iloc[-1], 2),
        "j": round(j.iloc[-1], 2),
    }


def _calc_boll(close: pd.Series, period: int = 20, std_dev: int = 2) -> dict:
    """布林带"""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return {
        "upper": round(upper.iloc[-1], 2),
        "mid": round(mid.iloc[-1], 2),
        "lower": round(lower.iloc[-1], 2),
    }


def _calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> dict:
    """ATR"""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    return {"atr": round(atr.iloc[-1], 4), "period": period}
