"""
indicators.py
-------------
Technical indicators built with plain pandas/numpy so there are no painful
dependencies (no TA-Lib). Every function takes a DataFrame of OHLCV bars with
columns: open, high, low, close, volume — and returns a pandas Series.

Learning note: indicators are just rolling math over price. Nothing magic.
"""
from __future__ import annotations

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
