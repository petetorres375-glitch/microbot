"""
Tests for microbot/rsi2_variants.py.

_resolve_exit() is tested directly with hand-built price paths — no need to
reverse-engineer real RSI(2)/SMA math to control when each exit fires.
backtest_rsi2_variant() gets one lightweight integration smoke test using the
same synthetic-uptrend-then-dip pattern already used in test_strategies.py,
just to confirm entry-detection and exit-resolution are wired together
correctly (not to pin exact values).

Run: python -m pytest tests/test_rsi2_variants.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from microbot.rsi2_variants import _resolve_exit, backtest_rsi2_variant


# ---- helpers ----------------------------------------------------------------

def _bars(closes, opens=None, lows=None):
    """Build a minimal OHLC frame from a list of closes (open/low default to
    the close, i.e. no gap and no intrabar wick, unless overridden)."""
    n = len(closes)
    dates = pd.bdate_range("2024-01-01", periods=n)
    opens = opens if opens is not None else list(closes)
    lows = lows if lows is not None else list(closes)
    return pd.DataFrame({
        "open": opens, "high": [max(o, c) for o, c in zip(opens, closes)],
        "low": lows, "close": closes,
    }, index=dates)


# ---- _resolve_exit ------------------------------------------------------------

def test_strength_exit_fires_on_close_above_short_ma():
    # entry at bar 0, price=100. short_ma stays at 100 for bars 1-2 (close
    # below it), then bar 3's close (105) crosses above it.
    df = _bars([100, 98, 97, 105])
    short_ma = pd.Series([100, 100, 100, 100], index=df.index)
    price, idx, outcome = _resolve_exit(
        df, entry_idx=0, entry_price=100.0, short_ma=short_ma, atr_at_entry=1.0,
        variant="strength", catastrophic_atr_mult=6.0, time_stop_days=None)
    assert outcome == "strength"
    assert idx == 3
    assert price == 105


def test_catastrophic_stop_fires_via_intrabar_low_before_strength():
    # notional_risk = 6.0 * 1.0 = 6.0 -> catastrophic_stop = 94.0.
    # Bar 1: low touches 93 (below stop) AND close (101) is also above
    # short_ma (100) — the stop must still win (checked first).
    df = _bars(closes=[100, 101], opens=[100, 99], lows=[100, 93])
    short_ma = pd.Series([100, 100], index=df.index)
    price, idx, outcome = _resolve_exit(
        df, entry_idx=0, entry_price=100.0, short_ma=short_ma, atr_at_entry=1.0,
        variant="strength_cat", catastrophic_atr_mult=6.0, time_stop_days=None)
    assert outcome == "catastrophic_stop"
    assert idx == 1
    assert price == 94.0  # fills at the stop level, not the wick low


def test_catastrophic_stop_fires_via_gap_open():
    # Bar 1 opens at 90, already below the 94.0 catastrophic stop -> fills
    # at the open (gap-through), same convention as the main backtester.
    df = _bars(closes=[100, 91], opens=[100, 90], lows=[100, 89])
    short_ma = pd.Series([100, 100], index=df.index)
    price, idx, outcome = _resolve_exit(
        df, entry_idx=0, entry_price=100.0, short_ma=short_ma, atr_at_entry=1.0,
        variant="strength_cat", catastrophic_atr_mult=6.0, time_stop_days=None)
    assert outcome == "catastrophic_stop"
    assert price == 90.0


def test_time_stop_fires_when_neither_other_exit_happens():
    # Price never hits the stop and never closes above short_ma. With
    # time_stop_days=3, the exit should fire exactly on the 3rd day held.
    df = _bars([100, 98, 97, 96, 95])
    short_ma = pd.Series([100, 100, 100, 100, 100], index=df.index)
    price, idx, outcome = _resolve_exit(
        df, entry_idx=0, entry_price=100.0, short_ma=short_ma, atr_at_entry=1.0,
        variant="strength_cat_time", catastrophic_atr_mult=6.0, time_stop_days=3)
    assert outcome == "time_stop"
    assert idx == 3
    assert price == 96


def test_strength_beats_time_stop_on_the_same_day():
    # Day 3 both crosses above short_ma AND reaches the time-stop day count
    # -> strength must win since it's checked first.
    df = _bars([100, 98, 97, 105])
    short_ma = pd.Series([100, 100, 100, 100], index=df.index)
    price, idx, outcome = _resolve_exit(
        df, entry_idx=0, entry_price=100.0, short_ma=short_ma, atr_at_entry=1.0,
        variant="strength_cat_time", catastrophic_atr_mult=6.0, time_stop_days=3)
    assert outcome == "strength"


def test_open_end_when_no_exit_condition_ever_met():
    df = _bars([100, 98, 97, 96])
    short_ma = pd.Series([100, 100, 100, 100], index=df.index)
    price, idx, outcome = _resolve_exit(
        df, entry_idx=0, entry_price=100.0, short_ma=short_ma, atr_at_entry=1.0,
        variant="strength", catastrophic_atr_mult=6.0, time_stop_days=None)
    assert outcome == "open_end"
    assert idx == len(df) - 1
    assert price == 96


# ---- backtest_rsi2_variant (integration smoke test) --------------------------

def _uptrend_then_dip_df() -> pd.DataFrame:
    """500-bar uptrend (10->200) + a sharp dip, matching the pattern already
    used in test_strategies.py for MeanReversion, then a recovery leg so the
    "strength" exit actually has something to fire on."""
    n = 520
    dates = pd.bdate_range("2021-01-01", periods=n)
    prices = np.zeros(n)
    prices[:500] = 10.0 + np.arange(500) * 0.38          # steady uptrend
    prices[500:506] = [200.0, 195.0, 190.0, 185.0, 180.0, 179.0]  # sharp dip
    prices[506:] = 179.0 + np.arange(n - 506) * 3.0       # sharp recovery
    return pd.DataFrame({
        "open": prices - 0.5, "high": prices + 2.0,
        "low": prices - 2.0, "close": prices,
        "volume": np.full(n, 1_500_000),
    }, index=dates)


def test_backtest_rsi2_variant_produces_at_least_one_trade():
    df = _uptrend_then_dip_df()
    trades = backtest_rsi2_variant(df, "strength")
    assert len(trades) >= 1
    assert trades[0]["outcome"] in {"strength", "open_end"}
