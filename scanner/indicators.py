"""
Replicates ManhDucCapital.pine indicator logic in Python.

SuperTrend: ATR-based trend line with "ratchet" mechanism (identical to Pine Script).
BiasNorm: 9-point bull/bear score normalized to [0, 100].
"""

import numpy as np
import pandas as pd

from scanner.config import (
    ADX_PERIOD, EMA_FAST, EMA_SLOW, MACD_FAST, MACD_SIGNAL, MACD_SLOW,
    OBV_EMA_PERIOD, RSI_PERIOD, RSI_SMOOTH, ST_MULT_LONG, ST_MULT_SHORT,
    ST_PERIOD_LONG, ST_PERIOD_SHORT, STOCH_D_PERIOD, STOCH_K_PERIOD,
    STOCH_SMOOTH, VOL_AVG_PERIOD,
)


def calc_supertrend(
    df: pd.DataFrame,
    period: int | None = None,
    multiplier: float | None = None,
    style: str = "long",
) -> pd.DataFrame:
    """
    Compute SuperTrend and return df with added columns:
      atr, st_up, st_dn, trend (1=bull/-1=bear), buy_signal, sell_signal

    Replicates Pine Script ratchet:
      up  = max(up, up_prev)  only when close_prev > up_prev
      dn  = min(dn, dn_prev)  only when close_prev < dn_prev
    """
    if period is None:
        period = ST_PERIOD_LONG if style == "long" else ST_PERIOD_SHORT
    if multiplier is None:
        multiplier = ST_MULT_LONG if style == "long" else ST_MULT_SHORT

    df = df.copy()
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    n = len(df)

    # ATR via Wilder's RMA (same as Pine ta.atr)
    atr = _wilder_atr(high, low, close, period)
    df["atr"] = atr

    src = (high + low) / 2.0  # hl2

    # Raw bands
    up_raw = src - multiplier * atr
    dn_raw = src + multiplier * atr

    # Apply ratchet mechanism (must loop — each bar depends on previous)
    up = up_raw.copy()
    dn = dn_raw.copy()
    trend = np.ones(n, dtype=int)

    for i in range(1, n):
        # Ratchet: up only increases, dn only decreases
        up[i] = max(up[i], up[i - 1]) if close[i - 1] > up[i - 1] else up[i]
        dn[i] = min(dn[i], dn[i - 1]) if close[i - 1] < dn[i - 1] else dn[i]

        # Trend flip
        if trend[i - 1] == -1 and close[i] > dn[i - 1]:
            trend[i] = 1
        elif trend[i - 1] == 1 and close[i] < up[i - 1]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]

    df["st_up"] = up
    df["st_dn"] = dn
    df["trend"] = trend
    df["supertrend"] = np.where(trend == 1, up, dn)

    # Signals: flip on last bar
    trend_s = pd.Series(trend, index=df.index)
    df["buy_signal"] = (trend_s == 1) & (trend_s.shift(1) == -1)
    df["sell_signal"] = (trend_s == -1) & (trend_s.shift(1) == 1)

    return df


def get_supertrend_state(df: pd.DataFrame, style: str = "long") -> dict:
    """
    Tính ST trên df lịch sử, trả về trạng thái bar cuối để dùng cho incremental update.
    Gọi 1 lần lúc khởi động, sau đó dùng calc_supertrend_next() mỗi bar mới.
    """
    df = calc_supertrend(df, style=style)
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last
    period = ST_PERIOD_LONG if style == "long" else ST_PERIOD_SHORT
    mult   = ST_MULT_LONG   if style == "long" else ST_MULT_SHORT
    return {
        "atr":       float(last["atr"]),
        "up":        float(last["st_up"]),
        "dn":        float(last["st_dn"]),
        "trend":     int(last["trend"]),
        "close":     float(last["close"]),
        "period":    period,
        "mult":      mult,
    }


def calc_supertrend_next(
    state: dict,
    high: float, low: float, close: float,
) -> dict:
    """
    Tính ST cho 1 bar mới dựa trên state từ bar trước — O(1).
    Trả về state mới + tín hiệu.
    """
    period = state["period"]
    mult   = state["mult"]
    prev_close = state["close"]
    prev_atr   = state["atr"]
    prev_up    = state["up"]
    prev_dn    = state["dn"]
    prev_trend = state["trend"]

    # Wilder ATR (1 bước)
    tr  = max(high - low, abs(high - prev_close), abs(low - prev_close))
    atr = (prev_atr * (period - 1) + tr) / period

    src     = (high + low) / 2.0
    up_raw  = src - mult * atr
    dn_raw  = src + mult * atr

    # Ratchet
    up = max(up_raw, prev_up) if prev_close > prev_up else up_raw
    dn = min(dn_raw, prev_dn) if prev_close < prev_dn else dn_raw

    # Trend flip
    if prev_trend == -1 and close > prev_dn:
        trend = 1
    elif prev_trend == 1 and close < prev_up:
        trend = -1
    else:
        trend = prev_trend

    return {
        "atr":        atr,
        "up":         up,
        "dn":         dn,
        "trend":      trend,
        "close":      close,
        "period":     period,
        "mult":       mult,
        "supertrend": up if trend == 1 else dn,
        "buy_signal": trend == 1 and prev_trend == -1,
        "sell_signal":trend == -1 and prev_trend == 1,
    }


def calc_bias_norm(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all 9 bull + 9 bear criteria and produce bias_norm [0, 100].
    Replicates Pine Script BiasNorm exactly.
    """
    df = df.copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    open_ = df["open"]

    # --- Indicators ---
    ema9 = close.ewm(span=EMA_FAST, adjust=False).mean()
    ema21 = close.ewm(span=EMA_SLOW, adjust=False).mean()

    # VWAP on daily bars: Pine resets per session.
    # On daily OHLCV, close > vwap ≡ close > hlc3 ≡ 2*close > high+low
    hlc3 = (high + low + close) / 3
    # Use cumulative VWAP approximation per calendar year
    df["_vwap"] = hlc3  # simplified: use hlc3 as daily "vwap"

    # RSI with EMA smoothing
    rsi_raw = _rsi(close, RSI_PERIOD)
    rsi_val = rsi_raw.ewm(span=RSI_SMOOTH, adjust=False).mean()

    # MACD
    ema_fast = close.ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=MACD_SLOW, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    macd_signal_line = macd_line.ewm(span=MACD_SIGNAL, adjust=False).mean()
    macd_hist = macd_line - macd_signal_line
    macd_hist_prev = macd_hist.shift(1)

    # ADX
    adx = _adx(high, low, close, ADX_PERIOD)

    # OBV
    obv = _obv(close, volume)
    obv_ema = obv.ewm(span=OBV_EMA_PERIOD, adjust=False).mean()

    # Stochastic
    stoch_k = _stoch_k(close, high, low, STOCH_K_PERIOD)
    stoch_d = stoch_k.rolling(STOCH_D_PERIOD).mean()
    stoch_k_smooth = stoch_k.ewm(span=STOCH_SMOOTH, adjust=False).mean()

    # Volume average
    vol_avg = volume.rolling(VOL_AVG_PERIOD).mean()

    # --- Bull Score (9 criteria) ---
    b1 = (ema9 > ema21).astype(int)
    b2 = (close > hlc3).astype(int)          # close > vwap (daily approximation)
    b3 = (rsi_val > 52).astype(int)
    b4 = ((macd_hist > 0) & (macd_hist > macd_hist_prev)).astype(int)
    b5 = (adx > 20).astype(int)
    b6 = (obv > obv_ema).astype(int)
    b7 = ((stoch_k_smooth > stoch_d) & (stoch_k_smooth < 80)).astype(int)
    b8 = (close > (open_ + high) / 2).astype(int)
    b9 = (volume > vol_avg).astype(int)

    b_score = b1 + b2 + b3 + b4 + b5 + b6 + b7 + b8 + b9

    # --- Bear Score (9 criteria) ---
    r1 = (ema9 < ema21).astype(int)
    r2 = (close < hlc3).astype(int)
    r3 = (rsi_val < 48).astype(int)
    r4 = ((macd_hist < 0) & (macd_hist < macd_hist_prev)).astype(int)
    r5 = (adx > 20).astype(int)              # same ADX condition as Pine
    r6 = (obv < obv_ema).astype(int)
    r7 = ((stoch_k_smooth < stoch_d) & (stoch_k_smooth > 20)).astype(int)
    r8 = (close < (open_ + low) / 2).astype(int)
    r9 = (volume > vol_avg).astype(int)      # same volume condition as Pine

    r_score = r1 + r2 + r3 + r4 + r5 + r6 + r7 + r8 + r9

    score_base = 9.0
    bull_pct = (b_score / score_base) * 100
    bear_pct = (r_score / score_base) * 100
    net_bias = bull_pct - bear_pct
    bias_norm = (net_bias + 100) / 2
    bias_norm = bias_norm.clip(0, 100)

    df["b_score"] = b_score
    df["r_score"] = r_score
    df["bias_norm"] = bias_norm
    df["bias_change"] = bias_norm - bias_norm.shift(3)

    # Store individual criteria for dashboard breakdown
    criteria_names = ["ema", "vwap", "rsi", "macd", "adx", "obv", "stoch", "candle", "vol"]
    for name, bull_col, bear_col in zip(
        criteria_names,
        [b1, b2, b3, b4, b5, b6, b7, b8, b9],
        [r1, r2, r3, r4, r5, r6, r7, r8, r9],
    ):
        df[f"bull_{name}"] = bull_col
        df[f"bear_{name}"] = bear_col

    return df


def analyze_ticker(
    df: pd.DataFrame, style: str = "long"
) -> dict:
    """
    Run both SuperTrend and BiasNorm on df, return last-bar summary dict.
    """
    df = calc_supertrend(df, style=style)
    df = calc_bias_norm(df)

    last = df.iloc[-1]
    criteria_names = ["ema", "vwap", "rsi", "macd", "adx", "obv", "stoch", "candle", "vol"]

    # ── Tìm lần lật SuperTrend gần nhất (y hệt bảng góc phải Pine Script) ──
    last_signal_type  = None
    last_signal_date  = None
    last_signal_price = None
    bars_since_signal = 0

    prev_buy_date  = None   # lần MUA trước tín hiệu BÁN gần nhất
    prev_buy_price = None

    for i in range(len(df) - 1, -1, -1):
        if df["buy_signal"].iloc[i]:
            last_signal_type  = "MUA"
            last_signal_date  = df.index[i]
            last_signal_price = float(df["low"].iloc[i])   # mua = low
            bars_since_signal = len(df) - 1 - i
            break
        elif df["sell_signal"].iloc[i]:
            last_signal_type  = "BÁN"
            last_signal_date  = df.index[i]
            last_signal_price = float(df["high"].iloc[i])  # bán = high
            bars_since_signal = len(df) - 1 - i
            # Tìm lần MUA gần nhất trước tín hiệu BÁN này
            for j in range(i - 1, -1, -1):
                if df["buy_signal"].iloc[j]:
                    prev_buy_date  = df.index[j]
                    prev_buy_price = float(df["low"].iloc[j])  # mua = low
                    break
            break

    # PnL từ giá tín hiệu đến hiện tại
    signal_pnl_pct = None
    if last_signal_price and last_signal_price > 0:
        close_now = float(df["close"].iloc[-1])
        if last_signal_type == "MUA":
            signal_pnl_pct = round((close_now - last_signal_price) / last_signal_price * 100, 2)
        else:
            # Bán rồi → PnL là lãi/lỗ nếu đã thoát lệnh
            signal_pnl_pct = round((last_signal_price - close_now) / last_signal_price * 100, 2)

    # Hỗ trợ = st_up (đường dưới), Kháng cự = st_dn (đường trên)
    support    = float(last["st_up"])
    resistance = float(last["st_dn"])

    # Khoảng cách % từ giá đến hỗ trợ / kháng cự
    close_val = float(last["close"])
    dist_support    = (close_val - support)    / support    * 100 if support    else None
    dist_resistance = (resistance - close_val) / close_val * 100 if resistance else None

    # ── Siêu cổ phiếu — 7 tiêu chí từ ManhDucCapital.pine ──────────────────
    close_s  = df["close"]
    vol_s    = df["volume"]
    ema50    = close_s.ewm(span=50,  adjust=False).mean()
    ema200   = close_s.ewm(span=200, adjust=False).mean()

    _e200_now  = float(ema200.iloc[-1])
    _e200_prev = float(ema200.iloc[-21]) if len(ema200) >= 21 else _e200_now
    _e50_now   = float(ema50.iloc[-1])
    _high52w   = float(close_s.rolling(min(252, len(close_s))).max().iloc[-1])
    _adx_now   = float(_adx(df["high"], df["low"], close_s, 14).iloc[-1])
    _vol20     = float(vol_s.rolling(20).mean().iloc[-1])
    _vol60     = float(vol_s.rolling(min(60, len(vol_s))).mean().iloc[-1])

    ss1 = close_val > _e200_now                      # Giá > EMA200
    ss2 = _e200_now > _e200_prev                     # EMA200 đang tăng
    ss3 = close_val >= _high52w * 0.90               # Gần/phá đỉnh 52 tuần
    ss4 = _e50_now  > _e200_now                      # EMA50 > EMA200
    ss5 = close_val > _e50_now                       # Giá > EMA50
    ss6 = _adx_now  > 25                             # ADX > 25
    ss7 = _vol20    > _vol60                         # Volume 20p > 60p

    super_score    = sum([ss1, ss2, ss3, ss4, ss5, ss6, ss7])
    is_super_stock = super_score >= 5

    # VSR levels (Volume Support/Resistance — port of Pine _vsr_calc)
    vsr_sup, vsr_res = _calc_vsr_levels(df)
    vsr_near_breakout   = False
    vsr_dist_to_res_pct = None
    vsr_base_width_pct  = None
    if not (np.isnan(vsr_sup) or np.isnan(vsr_res)) and vsr_sup < vsr_res:
        if vsr_sup < close_val < vsr_res:
            vsr_dist_to_res_pct = round((vsr_res - close_val) / close_val * 100, 2)
            vsr_base_width_pct  = round((vsr_res - vsr_sup)  / vsr_sup  * 100, 2)
            vsr_near_breakout   = vsr_dist_to_res_pct <= 7.0

    return {
        "close": close_val,
        "trend": int(last["trend"]),
        "supertrend": float(last["supertrend"]),
        "support": support,
        "resistance": resistance,
        "dist_support_pct": round(dist_support, 2) if dist_support is not None else None,
        "dist_resistance_pct": round(dist_resistance, 2) if dist_resistance is not None else None,
        "buy_signal": bool(last["buy_signal"]),
        "sell_signal": bool(last["sell_signal"]),
        "bias_norm": round(float(last["bias_norm"]), 1),
        "b_score": int(last["b_score"]),
        "r_score": int(last["r_score"]),
        "atr": float(last["atr"]),
        "volume": float(last["volume"]),
        # Giá vnstock trả về đơn vị nghìn VND (48.5 = 48,500đ) → nhân 1000 để ra VND thực
        "turnover": float(last["volume"]) * float(last["close"]) * 1000,
        "vol_avg": last.get("_vol_avg", float("nan")),
        "bull_criteria": {n: bool(last[f"bull_{n}"]) for n in criteria_names},
        "bear_criteria": {n: bool(last[f"bear_{n}"]) for n in criteria_names},
        # Thông tin lệnh gần nhất (y hệt bảng góc phải Pine Script)
        "last_signal_type":  last_signal_type,
        "last_signal_date":  last_signal_date,
        "last_signal_price": last_signal_price,
        "bars_since_signal": bars_since_signal,
        "signal_pnl_pct":    signal_pnl_pct,
        # Lần MUA trước tín hiệu BÁN (dùng cho P&L báo cáo cuối phiên)
        "prev_buy_date":     prev_buy_date,
        "prev_buy_price":    prev_buy_price,
        # Siêu cổ phiếu 7 tiêu chí
        "ss1": ss1, "ss2": ss2, "ss3": ss3, "ss4": ss4,
        "ss5": ss5, "ss6": ss6, "ss7": ss7,
        "super_score":    super_score,
        "is_super_stock": is_super_stock,
        # VSR — vùng hỗ trợ/kháng cự theo khối lượng
        "vsr_sup":              vsr_sup if not np.isnan(vsr_sup) else None,
        "vsr_res":              vsr_res if not np.isnan(vsr_res) else None,
        "vsr_near_breakout":    vsr_near_breakout,
        "vsr_dist_to_res_pct":  vsr_dist_to_res_pct,
        "vsr_base_width_pct":   vsr_base_width_pct,
    }


# --- Internal helpers ---

def _wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """ATR using Wilder's smoothing (RMA), matching Pine ta.atr."""
    n = len(close)
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))

    atr = np.zeros(n)
    atr[period - 1] = tr[:period].mean()
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def _calc_vsr_levels(
    df: pd.DataFrame, lb: int = 20, vol_len: int = 2
) -> tuple[float, float]:
    """
    Port of Pine Script _vsr_calc.  Returns (sup_lvl, res_lvl).

    Logic:
      - sup_lvl: pivot low confirmed while current bar has a buying-volume spike
      - res_lvl: pivot high confirmed while current bar has a selling-volume spike
    Both NaN when insufficient data or no qualifying pivot found.
    """
    n = len(df)
    if n < 2 * lb + 1:
        return float("nan"), float("nan")

    close = df["close"]
    open_ = df["open"]
    high  = df["high"]
    low   = df["low"]
    vol   = df["volume"]

    # Signed volume per bar (_vsr_delta in Pine): +vol for bull candle, -vol for bear
    delta = pd.Series(
        np.where(close > open_, vol, np.where(close < open_, -vol, 0.0)),
        index=df.index,
    )

    # Volume thresholds (Pine: ta.highest/lowest of Vol/2.5 over vsr_vol_len)
    vol_hi = (delta / 2.5).rolling(vol_len, min_periods=1).max()
    vol_lo = (delta / 2.5).rolling(vol_len, min_periods=1).min()

    # Pivot detection: ta.pivothigh(src, lb, lb) at bar i returns high[i-lb]
    # if high[i-lb] == max(high[i-2lb : i+1])
    roll_max = high.rolling(2 * lb + 1, min_periods=2 * lb + 1).max()
    roll_min = low.rolling(2 * lb + 1,  min_periods=2 * lb + 1).min()

    piv_high = high.shift(lb).where(high.shift(lb) == roll_max)
    piv_low  = low.shift(lb).where(low.shift(lb)   == roll_min)

    # Support: pivot low confirmed + strong buying delta on confirmation bar
    sup_mask = piv_low.notna()  & (delta > vol_hi)
    res_mask = piv_high.notna() & (delta < vol_lo)

    sup_idx = sup_mask[sup_mask].index
    res_idx = res_mask[res_mask].index

    sup_lvl = float(piv_low.loc[sup_idx[-1]])  if len(sup_idx) > 0 else float("nan")
    res_lvl = float(piv_high.loc[res_idx[-1]]) if len(res_idx) > 0 else float("nan")

    return sup_lvl, res_lvl


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """ADX using Wilder smoothing. Returns ADX series."""
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    high_a = high.values
    low_a = low.values
    close_a = close.values
    n = len(close_a)
    tr = np.zeros(n)
    tr[0] = high_a[0] - low_a[0]
    for i in range(1, n):
        tr[i] = max(high_a[i] - low_a[i], abs(high_a[i] - close_a[i - 1]), abs(low_a[i] - close_a[i - 1]))

    def wilder_smooth(arr: np.ndarray, p: int) -> np.ndarray:
        result = np.zeros(n)
        result[p - 1] = arr[:p].sum()
        for i in range(p, n):
            result[i] = result[i - 1] - result[i - 1] / p + arr[i]
        return result

    smooth_tr = wilder_smooth(tr, period)
    smooth_plus = wilder_smooth(plus_dm, period)
    smooth_minus = wilder_smooth(minus_dm, period)

    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = np.where(smooth_tr != 0, 100 * smooth_plus / smooth_tr, 0.0)
        minus_di = np.where(smooth_tr != 0, 100 * smooth_minus / smooth_tr, 0.0)
        dx = np.where(
            (plus_di + minus_di) != 0,
            100 * np.abs(plus_di - minus_di) / (plus_di + minus_di),
            0.0,
        )

    adx_arr = np.zeros(n)
    start = 2 * period - 2
    if start < n:
        adx_arr[start] = dx[period - 1: start + 1].mean()
        for i in range(start + 1, n):
            adx_arr[i] = (adx_arr[i - 1] * (period - 1) + dx[i]) / period

    return pd.Series(adx_arr, index=close.index)


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0))
    return (direction * volume).cumsum()


def _stoch_k(close: pd.Series, high: pd.Series, low: pd.Series, period: int) -> pd.Series:
    lowest = low.rolling(period).min()
    highest = high.rolling(period).max()
    denom = (highest - lowest).replace(0, np.nan)
    return 100 * (close - lowest) / denom
