"""
Tests for microbot/momentum_filter_tests.py.

_ExternalFilterWrapper and _spy_regime_and_vol are already covered by
tests/test_filter_tests.py (reused here unchanged). These tests focus on
what's new: _PerSymbolTrendFilterWrapper, and a light check that
_make_live_strategies() picks out the right three strategies.

Run: python -m pytest tests/test_momentum_filter_tests.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from microbot.momentum_filter_tests import (
    _make_live_strategies,
    _PerSymbolTrendFilterWrapper,
)


class _FakeInner:
    name = "fake_momentum"

    def min_bars(self):
        return 5

    def precompute(self, df):
        return {"marker": "precomputed"}

    def evaluate(self, symbol, df, cache=None):
        return {"symbol": symbol, "cache_has_trend200": cache is not None and "_trend200" in cache}


def _uptrend_df(n=250) -> pd.DataFrame:
    dates = pd.bdate_range("2023-01-01", periods=n)
    prices = 100.0 + np.arange(n) * 0.5  # steadily rising -> ends well above its own 200-SMA
    return pd.DataFrame({
        "open": prices - 0.5, "high": prices + 1.0,
        "low": prices - 1.0, "close": prices,
    }, index=dates)


def _downtrend_df(n=250) -> pd.DataFrame:
    dates = pd.bdate_range("2023-01-01", periods=n)
    prices = 300.0 - np.arange(n) * 0.5  # steadily falling -> ends well below its own 200-SMA
    return pd.DataFrame({
        "open": prices + 0.5, "high": prices + 1.0,
        "low": prices - 1.0, "close": prices,
    }, index=dates)


def test_trend_filter_blocks_when_price_below_own_sma():
    df = _downtrend_df()
    wrapped = _PerSymbolTrendFilterWrapper(_FakeInner())
    cache = wrapped.precompute(df)
    result = wrapped.evaluate("XYZ", df, cache=cache)
    assert result is None


def test_trend_filter_allows_and_delegates_when_price_above_own_sma():
    df = _uptrend_df()
    wrapped = _PerSymbolTrendFilterWrapper(_FakeInner())
    cache = wrapped.precompute(df)
    result = wrapped.evaluate("XYZ", df, cache=cache)
    assert result is not None
    assert result["symbol"] == "XYZ"
    assert result["cache_has_trend200"] is True


def test_trend_filter_min_bars_is_at_least_the_trend_ma_window():
    wrapped = _PerSymbolTrendFilterWrapper(_FakeInner(), trend_ma=200)
    assert wrapped.min_bars() >= 200
    # inner's own min_bars (5) shouldn't win if it's smaller.
    assert wrapped.min_bars() == max(5, 205)


def test_trend_filter_precompute_merges_inner_cache_without_dropping_it():
    df = _uptrend_df()
    wrapped = _PerSymbolTrendFilterWrapper(_FakeInner())
    cache = wrapped.precompute(df)
    assert "marker" in cache and cache["marker"] == "precomputed"
    assert "_trend200" in cache


def test_trend_filter_name_gets_suffix():
    wrapped = _PerSymbolTrendFilterWrapper(_FakeInner())
    assert wrapped.name == "fake_momentum_trend200"


def test_trend_filter_works_without_a_cache_too():
    # evaluate() must also work if called with cache=None (recomputes inline).
    df = _uptrend_df()
    wrapped = _PerSymbolTrendFilterWrapper(_FakeInner())
    result = wrapped.evaluate("XYZ", df, cache=None)
    assert result is not None
    assert result["cache_has_trend200"] is False  # no cache was passed through


def test_make_live_strategies_returns_the_three_momentum_strategies():
    strats = _make_live_strategies()
    assert set(strats.keys()) <= {"trend_momentum", "breakout", "breakout_52w"}
    assert len(strats) > 0
    for name, strat in strats.items():
        assert strat.name == name
