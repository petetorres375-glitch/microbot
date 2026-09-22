"""
Tests for microbot/mean_reversion_variants.py.

_resolve_exit() is already covered by tests/test_rsi2_variants.py (it's
generic and imported here unchanged), so these tests focus on what's new:
_entry_signal()'s conditions and the end-to-end wiring in
backtest_mr_variant() / backtest_mr_current_bracket().

Run: python -m pytest tests/test_mean_reversion_variants.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from microbot.mean_reversion_variants import (
    _entry_signal,
    backtest_mr_current_bracket,
    backtest_mr_variant,
)


def _uptrend_then_dip_df() -> pd.DataFrame:
    """500-bar uptrend (10->200) + a sharp dip + a recovery leg — the same
    pattern used elsewhere in this test suite (see test_strategies.py's
    _make_mr_df and test_rsi2_variants.py's _uptrend_then_dip_df) to get a
    controlled oversold-in-an-uptrend setup without hand-deriving RSI/BB math."""
    n = 520
    dates = pd.bdate_range("2021-01-01", periods=n)
    prices = np.zeros(n)
    prices[:500] = 10.0 + np.arange(500) * 0.38
    prices[500:506] = [200.0, 195.0, 190.0, 185.0, 180.0, 179.0]
    prices[506:] = 179.0 + np.arange(n - 506) * 3.0
    df = pd.DataFrame({
        "open": prices - 0.5, "high": prices + 2.0,
        "low": prices - 2.0, "close": prices,
        "volume": np.full(n, 1_500_000),
    }, index=dates)
    # The raw dip has a LOWER low on its last day (blocks the higher-low
    # gate by design — see test_strategies.py's _make_mr_df). Bump just that
    # one day's low above the prior day's, same technique used there, so
    # this fixture actually produces an entry.
    df.at[df.index[505], "low"] = df["low"].iloc[504] + 0.5
    return df


def test_entry_signal_fires_on_the_controlled_dip():
    df = _uptrend_then_dip_df()
    sig = _entry_signal(df)
    # The dip bottoms out at index 505 (the last of the 6 sharp-down days).
    assert sig["entry"].iloc[505] or sig["entry"].iloc[504]
    assert sig["entry"].sum() >= 1


def test_entry_signal_never_fires_during_pure_uptrend():
    # A clean uptrend has no oversold dip at all -> zero entries.
    n = 520
    dates = pd.bdate_range("2021-01-01", periods=n)
    prices = 10.0 + np.arange(n) * 0.38
    df = pd.DataFrame({
        "open": prices - 0.5, "high": prices + 1.0,
        "low": prices - 1.0, "close": prices,
        "volume": np.full(n, 1_000_000),
    }, index=dates)
    sig = _entry_signal(df)
    assert sig["entry"].sum() == 0


def test_backtest_mr_variant_produces_at_least_one_trade():
    df = _uptrend_then_dip_df()
    trades = backtest_mr_variant(df, "strength")
    assert len(trades) >= 1
    assert trades[0]["outcome"] in {"strength", "open_end"}


def test_backtest_mr_current_bracket_produces_at_least_one_trade():
    # With weekly_filter=False (this module's whole point), the current
    # bracket exit should fire on the same controlled dip.
    df = _uptrend_then_dip_df()
    trades = backtest_mr_current_bracket(df)
    assert len(trades) >= 1
    assert trades[0]["outcome"] in {"target", "stop", "open_end"}


def _uptrend_then_dip_df_lower_low_ending() -> pd.DataFrame:
    """Same as _uptrend_then_dip_df() but WITHOUT the higher-low bump — the
    raw dip pattern has a lower low on its final day by construction, which
    should block entry when require_higher_low=True and allow it through
    when require_higher_low=False."""
    n = 520
    dates = pd.bdate_range("2021-01-01", periods=n)
    prices = np.zeros(n)
    prices[:500] = 10.0 + np.arange(500) * 0.38
    prices[500:506] = [200.0, 195.0, 190.0, 185.0, 180.0, 179.0]
    prices[506:] = 179.0 + np.arange(n - 506) * 3.0
    return pd.DataFrame({
        "open": prices - 0.5, "high": prices + 2.0,
        "low": prices - 2.0, "close": prices,
        "volume": np.full(n, 1_500_000),
    }, index=dates)


def test_require_higher_low_true_blocks_a_lower_low_ending():
    df = _uptrend_then_dip_df_lower_low_ending()
    sig = _entry_signal(df, require_higher_low=True)
    assert sig["entry"].sum() == 0


def test_require_higher_low_false_unblocks_the_same_dip():
    df = _uptrend_then_dip_df_lower_low_ending()
    sig = _entry_signal(df, require_higher_low=False)
    assert sig["entry"].sum() >= 1


def test_backtest_mr_variant_require_higher_low_false_produces_a_trade_that_was_blocked():
    df = _uptrend_then_dip_df_lower_low_ending()
    assert backtest_mr_variant(df, "strength", require_higher_low=True) == []
    trades = backtest_mr_variant(df, "strength", require_higher_low=False)
    assert len(trades) >= 1


def test_backtest_mr_current_bracket_require_higher_low_false_produces_a_trade_that_was_blocked():
    df = _uptrend_then_dip_df_lower_low_ending()
    assert backtest_mr_current_bracket(df, require_higher_low=True) == []
    trades = backtest_mr_current_bracket(df, require_higher_low=False)
    assert len(trades) >= 1


def test_invalid_variant_raises():
    df = _uptrend_then_dip_df()
    try:
        backtest_mr_variant(df, "not_a_real_variant")
        assert False, "expected ValueError"
    except ValueError:
        pass
