"""
Tests for the weekly trend filter, sector cap, and MeanReversion higher-low filter.

Run: python -m pytest tests/test_strategies.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from microbot import indicators as ind
from microbot.engine import _sector_ok
from microbot.strategies import MeanReversion, TrendMomentum


# ---- helpers ---------------------------------------------------------------

def _uptrend_df(n: int = 200) -> pd.DataFrame:
    dates = pd.bdate_range("2023-01-01", periods=n)
    prices = 100.0 + np.arange(n) * 0.3
    return pd.DataFrame({
        "open": prices - 0.5, "high": prices + 1.0,
        "low": prices - 1.0, "close": prices,
        "volume": np.full(n, 1_000_000),
    }, index=dates)


def _downtrend_df(n: int = 200) -> pd.DataFrame:
    dates = pd.bdate_range("2023-01-01", periods=n)
    prices = 200.0 - np.arange(n) * 0.3
    return pd.DataFrame({
        "open": prices - 0.5, "high": prices + 1.0,
        "low": prices - 1.0, "close": prices,
        "volume": np.full(n, 1_000_000),
    }, index=dates)


def _make_mr_df() -> pd.DataFrame:
    """
    506-bar df: 500-bar uptrend (10→200) + 6-bar controlled dip (→179).
    Satisfies MeanReversion base conditions: close > SMA200, RSI < 32, close < lower BB.
    Default low[-1]=177 < low[-2]=178 (lower low — blocks the new filter).
    """
    n = 506
    dates = pd.bdate_range("2021-01-01", periods=n)
    prices = np.zeros(n)
    prices[:500] = 10.0 + np.arange(500) * 0.38   # 10 → ~200
    prices[500:] = [200.0, 195.0, 190.0, 185.0, 180.0, 179.0]
    return pd.DataFrame({
        "open": prices - 0.5,
        "high": prices + 2.0,
        "low": prices - 2.0,
        "close": prices,
        "volume": np.full(n, 1_500_000),
    }, index=dates)


# ---- weekly_ema_aligned ----------------------------------------------------

def test_weekly_aligned_uptrend():
    assert ind.weekly_ema_aligned(_uptrend_df(), period=10) is True


def test_weekly_aligned_downtrend():
    assert ind.weekly_ema_aligned(_downtrend_df(), period=10) is False


def test_weekly_aligned_insufficient_data_returns_true():
    # 25 bars ≈ 5 weeks — fewer than period+2=12 → falls back to True
    dates = pd.bdate_range("2023-01-01", periods=25)
    df = pd.DataFrame({"close": np.ones(25) * 100.0}, index=dates)
    assert ind.weekly_ema_aligned(df, period=10) is True


def test_weekly_aligned_no_datetime_index_returns_true():
    df = pd.DataFrame({"close": [100, 99, 98, 97, 96]})  # RangeIndex
    assert ind.weekly_ema_aligned(df) is True


def test_weekly_filter_blocks_signal_on_downtrend():
    # TrendMomentum with weekly_filter=True must return None on a downtrend df
    # even if the short-term indicators would otherwise fire.
    strat = TrendMomentum(weekly_filter=True)
    df = _downtrend_df(n=200)
    assert strat.evaluate("XYZ", df) is None


def test_weekly_filter_disabled_bypasses_check():
    # With weekly_filter=False the strategy evaluates on its own conditions only.
    # (It may or may not return a signal — we just confirm no exception and the
    #  weekly check is not the blocker on an uptrend df.)
    strat = TrendMomentum(weekly_filter=False)
    df = _uptrend_df(n=200)
    # Either None (conditions not met) or a Signal — both are valid; no crash.
    result = strat.evaluate("XYZ", df)
    assert result is None or hasattr(result, "entry")


# ---- MeanReversion higher-low filter ---------------------------------------

def test_mean_reversion_lower_low_blocked():
    """Default df has lower low (stock still falling): signal should be blocked."""
    df = _make_mr_df()
    strat = MeanReversion(weekly_filter=False)
    # Verify all original conditions hold (the filter is the only blocker).
    close = df["close"]
    assert close.iloc[-1] > ind.sma(close, 200).iloc[-1], "SMA200 uptrend check"
    assert ind.rsi(close, 14).iloc[-1] <= 32, "RSI oversold check"
    _, _, lower = ind.bollinger(close, 20)
    assert close.iloc[-1] <= lower.iloc[-1], "below BB check"
    assert df["low"].iloc[-1] < df["low"].iloc[-2], "lower low (test precondition)"
    assert strat.evaluate("XYZ", df) is None


def test_mean_reversion_higher_low_fires():
    """Same df but last bar has a higher low: signal should fire."""
    df = _make_mr_df()
    df_hl = df.copy()
    # Set low[-1] to just above low[-2] (realistic hammer bar: low > prior low but close lower).
    df_hl.at[df.index[-1], "low"] = df["low"].iloc[-2] + 0.5  # 178.5 > 178 ✓
    strat = MeanReversion(weekly_filter=False)
    sig = strat.evaluate("XYZ", df_hl)
    assert sig is not None
    assert sig.strategy == "mean_reversion"
    assert sig.stop < sig.entry < sig.target


# ---- sector cap (_sector_ok) -----------------------------------------------

def test_sector_ok_unknown_symbol():
    assert _sector_ok("UNKNOWN", {"GOOG"}, sector_map={"GOOG": "tech"}, max_same_sector=2) is True


def test_sector_ok_below_cap():
    smap = {"GOOG": "tech", "INTC": "tech", "CSCO": "tech"}
    assert _sector_ok("CSCO", {"GOOG"}, sector_map=smap, max_same_sector=2) is True


def test_sector_ok_at_cap():
    smap = {"GOOG": "tech", "INTC": "tech", "CSCO": "tech"}
    assert _sector_ok("CSCO", {"GOOG", "INTC"}, sector_map=smap, max_same_sector=2) is False


def test_sector_ok_different_sectors():
    smap = {"GOOG": "tech", "BTI": "consumer_staples", "RKLB": "industrial"}
    # 2 tech positions but asking about consumer_staples → allowed
    assert _sector_ok("BTI", {"GOOG", "INTC"}, sector_map=smap, max_same_sector=2) is True


def test_sector_ok_cap_one():
    smap = {"GOOG": "tech", "INTC": "tech"}
    assert _sector_ok("INTC", {"GOOG"}, sector_map=smap, max_same_sector=1) is False


def test_sector_ok_empty_held():
    smap = {"GOOG": "tech"}
    assert _sector_ok("GOOG", set(), sector_map=smap, max_same_sector=2) is True
