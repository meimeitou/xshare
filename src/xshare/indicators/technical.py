"""技术指标计算 - 纯 pandas 实现"""

import pandas as pd


def calculate_indicators(df: pd.DataFrame, indicators: list[str]) -> dict:
    """根据指定的指标列表计算技术指标"""
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)
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
        elif ind_upper == "VOL_MA":
            result["vol_ma"] = _calc_vol_ma(volume)
        elif ind_upper == "OBV":
            result["obv"] = _calc_obv(close, volume)
        elif ind_upper == "VWAP":
            amount = df["amount"].astype(float) if "amount" in df.columns else None
            result["vwap"] = _calc_vwap(close, volume, amount)
        elif ind_upper == "DMI":
            result["dmi"] = _calc_dmi(high, low, close)
        elif ind_upper == "NINE_TURN":
            result["nine_turn"] = _calc_nine_turn(close)
        elif ind_upper == "TREND":
            result["trend"] = _calc_trend(close)

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


def _calc_vol_ma(volume: pd.Series) -> dict:
    """成交量均线"""
    latest = {}
    for period in [5, 10, 20]:
        ma = volume.rolling(period).mean()
        latest[f"vol_ma{period}"] = round(ma.iloc[-1], 0) if len(ma) >= period else None
    latest["volume"] = round(volume.iloc[-1], 0)
    # 量比 = 当日成交量 / 5日均量
    vol_ma5 = volume.rolling(5).mean()
    if len(vol_ma5) >= 5 and vol_ma5.iloc[-1] > 0:
        latest["vol_ratio"] = round(volume.iloc[-1] / vol_ma5.iloc[-1], 2)
    return latest


def _calc_obv(close: pd.Series, volume: pd.Series) -> dict:
    """OBV 能量潮"""
    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv = (volume * direction).cumsum()
    # 返回最新值及 5 日变化
    obv_latest = round(obv.iloc[-1], 0)
    obv_5d_ago = round(obv.iloc[-6], 0) if len(obv) >= 6 else None
    result = {"obv": obv_latest}
    if obv_5d_ago is not None:
        result["obv_change_5d"] = round(obv_latest - obv_5d_ago, 0)
    return result


def _calc_vwap(close: pd.Series, volume: pd.Series, amount: pd.Series | None = None) -> dict:
    """VWAP 成交量加权平均价（当日）"""
    if amount is not None and amount.iloc[-1] > 0 and volume.iloc[-1] > 0:
        vwap = amount.iloc[-1] / volume.iloc[-1]
    else:
        # fallback: 用收盘价近似
        vwap = (close * volume).iloc[-20:].sum() / volume.iloc[-20:].sum()
    return {"vwap": round(vwap, 2), "close": round(close.iloc[-1], 2)}


def _calc_nine_turn(close: pd.Series, lookback: int = 4) -> dict:
    """神奇九转（TD Sequential）
    规则：收盘价连续高于/低于 lookback 天前的收盘价，计数到 9 为反转信号
    """
    ref = close.shift(lookback)
    up = (close > ref).astype(int)
    down = (close < ref).astype(int)

    # 计算连续计数
    up_count = pd.Series(0, index=close.index)
    down_count = pd.Series(0, index=close.index)
    for i in range(1, len(close)):
        if up.iloc[i] == 1:
            up_count.iloc[i] = up_count.iloc[i - 1] + 1
        else:
            up_count.iloc[i] = 0
        if down.iloc[i] == 1:
            down_count.iloc[i] = down_count.iloc[i - 1] + 1
        else:
            down_count.iloc[i] = 0

    latest_up = int(up_count.iloc[-1])
    latest_down = int(down_count.iloc[-1])

    result = {
        "up_count": latest_up,
        "down_count": latest_down,
    }

    # 信号判定
    if latest_up >= 9:
        result["signal"] = "九转见顶"
        result["hint"] = "连续上涨计数达9，可能阶段性见顶，注意风险"
    elif latest_down >= 9:
        result["signal"] = "九转见底"
        result["hint"] = "连续下跌计数达9，可能阶段性见底，关注机会"
    elif latest_up >= 7:
        result["signal"] = f"上涨计数{latest_up}，接近九转"
    elif latest_down >= 7:
        result["signal"] = f"下跌计数{latest_down}，接近九转"
    else:
        result["signal"] = "无九转信号"

    return result


def _calc_trend(close: pd.Series) -> dict:
    """趋势阶段判断（基于均线排列 + 价格位置）"""
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()

    c = close.iloc[-1]
    m5, m10, m20 = ma5.iloc[-1], ma10.iloc[-1], ma20.iloc[-1]
    m60 = ma60.iloc[-1] if len(close) >= 60 else None

    # 均线排列判定
    if m5 > m10 > m20:
        arrangement = "多头排列"
    elif m5 < m10 < m20:
        arrangement = "空头排列"
    else:
        arrangement = "交叉整理"

    # 综合阶段
    if arrangement == "多头排列" and c > m5:
        phase = "上升趋势"
    elif arrangement == "多头排列" and c < m5:
        phase = "上升趋势回调"
    elif arrangement == "空头排列" and c < m5:
        phase = "下降趋势"
    elif arrangement == "空头排列" and c > m5:
        phase = "下降趋势反弹"
    else:
        phase = "震荡整理"

    # 价格偏离度（相对 MA20）
    bias = round((c - m20) / m20 * 100, 2) if m20 > 0 else None

    result = {
        "phase": phase,
        "arrangement": arrangement,
        "ma5": round(m5, 2),
        "ma10": round(m10, 2),
        "ma20": round(m20, 2),
        "bias_ma20": bias,
    }
    if m60 is not None:
        result["ma60"] = round(m60, 2)
        result["above_ma60"] = c > m60
    return result


def _calc_dmi(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> dict:
    """DMI 趋向指标（+DI / -DI / ADX）"""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(span=period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(span=period, adjust=False).mean()

    return {
        "plus_di": round(plus_di.iloc[-1], 2),
        "minus_di": round(minus_di.iloc[-1], 2),
        "adx": round(adx.iloc[-1], 2),
    }
