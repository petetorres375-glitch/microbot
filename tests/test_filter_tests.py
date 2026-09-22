"""
Tests for microbot/filter_tests.py.

_ExternalFilterWrapper is tested with a fake "inner" strategy so the tests
don't depend on real Signal internals. _spy_regime_and_vol's percentile rank
gets a real no-lookahead regression check — the whole point of using an
expanding (not full-sample) rank is that trimming the future off the series
must not change values computed for the past.

Run: python -m pytest tests/test_filter_tests.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from microbot.filter_tests import _ExternalFilterWrapper, _spy_regime_and_vol


# ---- _ExternalFilterWrapper --------------------------------------------------

class _FakeInner:
    name = "fake_strategy"

    def min_bars(self):
        return 5

    def precompute(self, df):
        return {"marker": "precomputed"}

    def evaluate(self, symbol, df, cache=None):
        return {"symbol": symbol, "cache": cache}


def test_wrapper_blocks_when_date_not_ok():
    dates = pd.bdate_range("2024-01-01", periods=3)
    ok_by_date = pd.Series([True, False, True], index=dates)
    inner = _FakeInner()
    wrapped = _ExternalFilterWrapper(inner, ok_by_date, "_test")

    df = pd.DataFrame({"close": [1, 2, 3]}, index=dates)
    result = wrapped.evaluate("XYZ", df.loc[:dates[1]])
    assert result is None


def test_wrapper_allows_and_delegates_when_date_ok():
    dates = pd.bdate_range("2024-01-01", periods=3)
    ok_by_date = pd.Series([True, False, True], index=dates)
    inner = _FakeInner()
    wrapped = _ExternalFilterWrapper(inner, ok_by_date, "_test")

    df = pd.DataFrame({"close": [1, 2, 3]}, index=dates)
    result = wrapped.evaluate("XYZ", df.loc[:dates[0]], cache={"x": 1})
    assert result == {"symbol": "XYZ", "cache": {"x": 1}}


def test_wrapper_delegates_min_bars_and_precompute_unchanged():
    dates = pd.bdate_range("2024-01-01", periods=3)
    ok_by_date = pd.Series([True, True, True], index=dates)
    inner = _FakeInner()
    wrapped = _ExternalFilterWrapper(inner, ok_by_date, "_test")

    assert wrapped.min_bars() == 5
    assert wrapped.precompute(pd.DataFrame()) == {"marker": "precomputed"}


def test_wrapper_name_gets_suffix():
    ok_by_date = pd.Series([True], index=pd.bdate_range("2024-01-01", periods=1))
    wrapped = _ExternalFilterWrapper(_FakeInner(), ok_by_date, "_regime")
    assert wrapped.name == "fake_strategy_regime"


def test_wrapper_treats_missing_date_as_not_ok():
    # A date not present in the filter Series (e.g. edge-of-history gaps)
    # should block, not raise or silently pass through.
    ok_by_date = pd.Series([True], index=pd.bdate_range("2024-01-01", periods=1))
    wrapped = _ExternalFilterWrapper(_FakeInner(), ok_by_date, "_test")
    df = pd.DataFrame({"close": [1]}, index=pd.bdate_range("2030-01-01", periods=1))
    assert wrapped.evaluate("XYZ", df) is None


# ---- _spy_regime_and_vol: no-lookahead regression check ----------------------

def _synthetic_spy_df(n=400) -> pd.DataFrame:
    """A price series with a calm first half and a volatile second half, so
    the percentile rank has something real to distinguish."""
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2020-01-01", periods=n)
    calm_returns = rng.normal(0, 0.003, n // 2)
    volatile_returns = rng.normal(0, 0.03, n - n // 2)
    returns = np.concatenate([calm_returns, volatile_returns])
    prices = 100 * np.cumprod(1 + returns)
    return pd.DataFrame({
        "open": prices, "high": prices * 1.001,
        "low": prices * 0.999, "close": prices,
    }, index=dates)


class _FakeMarketData:
    def __init__(self, df):
        self._df = df

    def bars(self, symbol, lookback_days=None):
        return self._df


def test_regime_and_vol_is_causal_no_lookahead(monkeypatch):
    """Truncating the SPY history to end at date d must not change the
    regime/vol values computed AT date d — that's what makes this filter
    safe to use in a walk-forward backtest instead of leaking the future.

    _spy_regime_and_vol() imports MarketData lazily (`from .data import
    MarketData`) INSIDE the function, so patching microbot.data.MarketData
    (not filter_tests.MarketData) is what actually takes effect.
    """
    import microbot.filter_tests as ft

    full_df = _synthetic_spy_df(400)
    cutoff = 350  # a date well past the expanding rank's warmup period

    monkeypatch.setattr("microbot.data.MarketData", lambda: _FakeMarketData(full_df))
    result_full = ft._spy_regime_and_vol()

    monkeypatch.setattr("microbot.data.MarketData",
                         lambda: _FakeMarketData(full_df.iloc[:cutoff]))
    result_truncated = ft._spy_regime_and_vol()

    check_date = full_df.index[cutoff - 1]
    assert result_full.loc[check_date, "regime_ok"] == result_truncated.loc[check_date, "regime_ok"]
    assert result_full.loc[check_date, "vol_ok"] == result_truncated.loc[check_date, "vol_ok"]


def test_regime_ok_reflects_price_above_sma(monkeypatch):
    import microbot.filter_tests as ft

    full_df = _synthetic_spy_df(400)
    close = full_df["close"]
    sma200 = close.rolling(200).mean()
    expected = close > sma200

    monkeypatch.setattr("microbot.data.MarketData", lambda: _FakeMarketData(full_df))
    result = ft._spy_regime_and_vol()

    common_dates = result.index.intersection(expected.dropna().index)
    assert (result.loc[common_dates, "regime_ok"] == expected.loc[common_dates]).all()
