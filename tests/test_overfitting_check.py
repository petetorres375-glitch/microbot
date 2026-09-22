"""
Tests for microbot/overfitting_check.py.

split_trades_by_period() is pure — no network, no backtesting — so it's
tested directly with made-up trades. run_is_oos_check() and
run_param_sensitivity() get one lightweight integration smoke test each,
using the same synthetic uptrend-then-dip pattern already used elsewhere in
this test suite, just to confirm the wiring (fetch -> backtest -> bucket /
sweep -> stats) works, not to pin exact numbers.

Run: python -m pytest tests/test_overfitting_check.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from microbot.overfitting_check import (
    run_is_oos_check,
    run_param_sensitivity,
    split_trades_by_period,
)


# ---- split_trades_by_period (pure) -------------------------------------------

def _trade(entry_idx):
    return {"entry_idx": entry_idx, "r_multiple": 1.0}


def test_split_at_exact_cutoff_goes_to_out_of_sample():
    # total_bars=100, is_split=0.75 -> cutoff=75. A trade entered exactly AT
    # the cutoff index belongs to out-of-sample, not in-sample.
    trades = [_trade(74), _trade(75), _trade(76)]
    is_trades, oos_trades = split_trades_by_period(trades, total_bars=100, is_split=0.75)
    assert [t["entry_idx"] for t in is_trades] == [74]
    assert [t["entry_idx"] for t in oos_trades] == [75, 76]


def test_split_all_in_sample():
    trades = [_trade(0), _trade(10), _trade(50)]
    is_trades, oos_trades = split_trades_by_period(trades, total_bars=100, is_split=0.75)
    assert len(is_trades) == 3
    assert len(oos_trades) == 0


def test_split_all_out_of_sample():
    trades = [_trade(80), _trade(90), _trade(99)]
    is_trades, oos_trades = split_trades_by_period(trades, total_bars=100, is_split=0.75)
    assert len(is_trades) == 0
    assert len(oos_trades) == 3


def test_split_empty_trades():
    is_trades, oos_trades = split_trades_by_period([], total_bars=100, is_split=0.75)
    assert is_trades == []
    assert oos_trades == []


def test_split_respects_custom_ratio():
    trades = [_trade(49), _trade(50)]
    is_trades, oos_trades = split_trades_by_period(trades, total_bars=100, is_split=0.5)
    assert [t["entry_idx"] for t in is_trades] == [49]
    assert [t["entry_idx"] for t in oos_trades] == [50]


# ---- integration smoke tests --------------------------------------------------

def _uptrend_then_dip_df() -> pd.DataFrame:
    """Same fixture used in test_rsi2_variants.py — 500-bar uptrend + sharp
    dip + recovery, enough for an rsi2_reversion entry to fire at least once."""
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


def test_run_is_oos_check_accepts_an_arbitrary_strategy():
    # A stand-in "strategy" that always signals on a fixed date, so we can
    # confirm run_is_oos_check() actually used the PASSED strat rather than
    # silently defaulting to RSI2Reversion.
    from microbot.strategies import Signal

    class _AlwaysSignalsOnce:
        name = "fake_momentum"
        _fired = False

        def min_bars(self):
            return 1

        def precompute(self, df):
            return {}

        def evaluate(self, symbol, df, cache=None):
            if not self._fired and len(df) == 5:
                self._fired = True
                return Signal(symbol, self.name, "buy", 100.0, 90.0, 120.0,
                              5.0, "test signal", 2.0)
            return None

    dates = pd.bdate_range("2024-01-01", periods=10)
    df = pd.DataFrame({
        "open": range(100, 110), "high": range(101, 111),
        "low": range(99, 109), "close": range(100, 110),
    }, index=dates)
    result = run_is_oos_check({"XYZ": df}, strat=_AlwaysSignalsOnce())
    total_trades = result["in_sample"]["trades"] + result["out_of_sample"]["trades"]
    assert total_trades == 1


def test_run_is_oos_check_returns_both_buckets():
    dfs = {"XYZ": _uptrend_then_dip_df()}
    result = run_is_oos_check(dfs)
    assert set(result.keys()) == {"in_sample", "out_of_sample"}
    # The dip is at bar ~505 of 520 (>75% through), so this fixture's one
    # trade should land in out-of-sample, not in-sample.
    assert result["out_of_sample"]["trades"] >= 1


def test_run_param_sensitivity_covers_the_full_grid():
    dfs = {"XYZ": _uptrend_then_dip_df()}
    result = run_param_sensitivity(dfs, rsi_buy_values=(10, 12),
                                    stop_mult_values=(3.0, 3.5))
    assert set(result.keys()) == {
        "rsi_buy=10_stop_mult=3", "rsi_buy=10_stop_mult=3.5",
        "rsi_buy=12_stop_mult=3", "rsi_buy=12_stop_mult=3.5",
    }
    for stats in result.values():
        assert "expectancy_r" in stats
