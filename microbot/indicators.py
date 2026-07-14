"""
indicators.py
-------------
Technical indicators built with plain pandas/numpy so there are no painful
dependencies (no TA-Lib). Every function takes a DataFrame of OHLCV bars with
columns: open, high, low, close, volume — and returns a pandas Series.

Learning note: indicators are just rolling math over price. Nothing magic.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd


def sma(close: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    return close.rolling(period).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential moving average (more weight on recent bars)."""
    return close.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (0-100). <30 oversold, >70 overbought."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder's smoothing (the standard RSI uses an EMA-like average)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average True Range — a measure of volatility in price units.
    We size stops off ATR so the stop distance adapts to how 'jumpy' a stock is.
    """
    high, low, prev_close = df["high"], df["low"], df["close"].shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False).mean()


def bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0):
    """Bollinger Bands: middle SMA, plus/minus num_std standard deviations."""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    return mid, mid + num_std * std, mid - num_std * std


def donchian(df: pd.DataFrame, period: int = 20):
    """Donchian channel: rolling highest-high and lowest-low (breakout system)."""
    upper = df["high"].rolling(period).max()
    lower = df["low"].rolling(period).min()
    return upper, lower


def weekly_ema_aligned(df: pd.DataFrame, period: int = 10) -> bool:
    """
    True if the weekly chart is in an uptrend: price is above a rising EMA(period).
    Resamples the daily bars to weekly Friday closes — no extra API call needed.
    Falls back to True (no filter) when the index isn't a DatetimeIndex or there
    aren't enough weekly bars to compute the EMA reliably.
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        return True
    try:
        weekly_close = df["close"].resample("W-FRI").last().dropna()
    except Exception:
        return True
    if len(weekly_close) < period + 2:
        return True
    w_ema = weekly_close.ewm(span=period, adjust=False).mean()
    above_ema = bool(weekly_close.iloc[-1] > w_ema.iloc[-1])
    ema_rising = bool(w_ema.iloc[-1] > w_ema.iloc[-2])
    return above_ema and ema_rising


def weekly_ema_aligned_series(df: pd.DataFrame, period: int = 10) -> pd.Series:
    """
    Vectorized equivalent of calling weekly_ema_aligned(df.iloc[:i+1]) for
    every row i - one resample + one ewm over the (short) completed-weekly
    series instead of the O(bars) repeated resample of the naive per-bar call
    (previously ~1.8s of a 6.7s single-symbol backtest, purely from calling
    resample() once per day on a growing window).

    Derivation: for a day i inside week k, the per-bar function's weekly_close
    series is [completed weekly closes before week k] + [close_i] (the
    in-progress week's close-so-far - there's no lookahead since a truncated
    window can only contribute its own last row to the current bucket).
    Because EMA_new = alpha*x + (1-alpha)*EMA_prev is a strict interpolation
    between EMA_prev and x, both "x > EMA_new" (above_ema) and
    "EMA_new > EMA_prev" (ema_rising) are algebraically equivalent to the same
    single condition x > EMA_prev (dividing through by the shared (1-alpha)
    factor). So the whole per-bar check collapses to: is today's close above
    the EMA computed through the END of the last fully completed week.
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(True, index=df.index)
    try:
        weekly_close = df["close"].resample("W-FRI").last().dropna()
    except Exception:
        return pd.Series(True, index=df.index)

    w_ema = weekly_close.ewm(span=period, adjust=False).mean()
    prev_ema = w_ema.shift(1)  # EMA through the end of the PRIOR completed week

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        week_period = df.index.to_period("W-FRI")
        weekly_period_index = weekly_close.index.to_period("W-FRI")
    prev_ema_lookup = pd.Series(prev_ema.values, index=weekly_period_index)

    result = pd.Series(True, index=df.index)
    close = df["close"]
    for pos, p in enumerate(weekly_period_index):
        mask = (week_period == p)
        if not mask.any():
            continue
        if pos < period + 1:  # matches original "len(weekly_close) < period + 2" fallback
            result[mask] = True
        else:
            result[mask] = (close[mask] > prev_ema_lookup.loc[p]).to_numpy()
    return result


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average Directional Index — trend STRENGTH (not direction), 0-100.
    >25 typically means a real trend is present. We use it as a trend filter.
    """
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = atr(df, period)  # reuse ATR as smoothed true range
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1 / period, adjust=False).mean() / tr.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1 / period, adjust=False).mean() / tr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0.0)
