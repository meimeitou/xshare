"""技术指标计算 - 纯 pandas 实现"""

import math

import pandas as pd


def calculate_indicators(df: pd.DataFrame, indicators: list[str]) -> dict:
    """根据指定的指标列表计算技术指标（最新值摘要，供 MCP/Agent）。"""
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


def build_chart_payload(df: pd.DataFrame, indicators: list[str] | None = None) -> dict:
    """为前端 K 线/指标图构造序列数据（与 calculate_indicators 摘要并存）。

    返回字段：
      bars: [{date, open, high, low, close, volume}, ...]
      MA / MA_periods, MACD, RSI, KDJ, BOLL（按请求的 indicators 选择性输出）
    """
    if df is None or df.empty:
        return {"bars": []}

    work = df.sort_values("trade_date").reset_index(drop=True)
    dates = [_fmt_date(d) for d in work["trade_date"]]
    close = work["close"].astype(float)
    high = work["high"].astype(float)
    low = work["low"].astype(float)
    volume = work["volume"].astype(float)

    bars = []
    for i in range(len(work)):
        bars.append({
            "date": dates[i],
            "open": _num(work["open"].iloc[i]),
            "high": _num(high.iloc[i]),
            "low": _num(low.iloc[i]),
            "close": _num(close.iloc[i]),
            "volume": int(volume.iloc[i]) if not _isna(volume.iloc[i]) else 0,
        })

    wanted = {i.upper() for i in (indicators or ["MA", "MACD", "RSI", "KDJ", "BOLL"])}
    out: dict = {"bars": bars}

    if "MA" in wanted:
        periods = [5, 10, 20, 60]
        ma_series = []
        for p in periods:
            ma = close.rolling(p).mean()
            ma_series.append([
                {"date": dates[i], "value": _num(ma.iloc[i])}
                for i in range(len(dates))
                if not _isna(ma.iloc[i])
            ])
        out["MA"] = ma_series
        out["MA_periods"] = periods

    if "MACD" in wanted:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        hist = (dif - dea) * 2
        out["MACD"] = [
            {
                "date": dates[i],
                "macd": _num(dif.iloc[i]),
                "signal": _num(dea.iloc[i]),
                "histogram": _num(hist.iloc[i]),
            }
            for i in range(len(dates))
            if not (_isna(dif.iloc[i]) or _isna(dea.iloc[i]))
        ]

    if "RSI" in wanted:
        rsi = _rsi_series(close, 14)
        out["RSI"] = [
            {"date": dates[i], "value": _num(rsi.iloc[i])}
            for i in range(len(dates))
            if not _isna(rsi.iloc[i])
        ]

    if "KDJ" in wanted:
        k, d, j = _kdj_series(high, low, close)
        out["KDJ"] = [
            {
                "date": dates[i],
                "k": _num(k.iloc[i]),
                "d": _num(d.iloc[i]),
                "j": _num(j.iloc[i]),
            }
            for i in range(len(dates))
            if not (_isna(k.iloc[i]) or _isna(d.iloc[i]))
        ]

    if "BOLL" in wanted:
        mid = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = mid + 2 * std
        lower = mid - 2 * std
        out["BOLL"] = [
            {
                "date": dates[i],
                "upper": _num(upper.iloc[i]),
                "middle": _num(mid.iloc[i]),
                "lower": _num(lower.iloc[i]),
            }
            for i in range(len(dates))
            if not _isna(mid.iloc[i])
        ]

    return out


def resample_ohlcv(df: pd.DataFrame, period: str) -> pd.DataFrame:
    """将日线重采样为周线/月线；daily 原样返回。"""
    p = (period or "daily").lower()
    if p in ("daily", "day", "d") or df.empty:
        return df
    work = df.sort_values("trade_date").copy()
    work["trade_date"] = pd.to_datetime(work["trade_date"])
    work = work.set_index("trade_date")
    rule = "W-FRI" if p in ("weekly", "week", "w") else "ME"
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    if "amount" in work.columns:
        agg["amount"] = "sum"
    if "code" in work.columns:
        agg["code"] = "last"
    out = work.resample(rule).agg(agg).dropna(subset=["close"]).reset_index()
    out["trade_date"] = out["trade_date"].dt.date
    return out


def _fmt_date(v) -> str:
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    s = str(v)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def _isna(v) -> bool:
    try:
        return v is None or (isinstance(v, float) and math.isnan(v)) or pd.isna(v)
    except Exception:
        return False


def _num(v) -> float | None:
    if _isna(v):
        return None
    f = float(v)
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, 4)


def json_safe(obj):
    """递归把 NaN/Inf 转成 None，保证 JSON 可序列化。

    向后兼容别名，委托给 :func:`xshare.utils.to_json_safe`（统一实现）。
    """
    from xshare.utils import to_json_safe

    return to_json_safe(obj)


def _rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    # avg_loss==0 → rs 为 inf，RSI 记为 100；两者皆 0 → NaN
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    both_zero = (avg_gain == 0) & (avg_loss == 0)
    loss_zero = (avg_loss == 0) & (avg_gain > 0)
    rsi = rsi.mask(both_zero, float("nan"))
    rsi = rsi.mask(loss_zero, 100.0)
    return rsi


def _kdj_series(
    high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    low_n = low.rolling(n).min()
    high_n = high.rolling(n).max()
    rsv = (close - low_n) / (high_n - low_n) * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def _calc_ma(close: pd.Series) -> dict:
    """均线"""
    latest = {}
    for period in [5, 10, 20, 60]:
        ma = close.rolling(period).mean()
        latest[f"ma{period}"] = _num(ma.iloc[-1]) if len(ma) >= period else None
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
        "dif": _num(dif.iloc[-1]),
        "dea": _num(dea.iloc[-1]),
        "macd": _num(macd_bar.iloc[-1]),
    }


def _calc_rsi(close: pd.Series, period: int = 14) -> dict:
    """RSI"""
    rsi = _rsi_series(close, period)
    return {"rsi": _num(rsi.iloc[-1]), "period": period}


def _calc_kdj(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9) -> dict:
    """KDJ"""
    k, d, j = _kdj_series(high, low, close, n)
    return {
        "k": _num(k.iloc[-1]),
        "d": _num(d.iloc[-1]),
        "j": _num(j.iloc[-1]),
    }


def _calc_boll(close: pd.Series, period: int = 20, std_dev: int = 2) -> dict:
    """布林带"""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return {
        "upper": _num(upper.iloc[-1]),
        "mid": _num(mid.iloc[-1]),
        "lower": _num(lower.iloc[-1]),
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
