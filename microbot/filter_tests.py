"""
filter_tests.py
----------------
Task 4: test a market-regime filter and a volatility filter (individually
and combined) on top of rsi2_reversion and mean_reversion, without changing
either strategy's own entry logic — both filters are external, date-based
gates derived from SPY, applied through a thin wrapper around the real
strategy instance rather than a code change to strategies.py.

Market regime filter: today's SPY close is above SPY's own 200-day SMA
("the broad market is healthy", not just the individual stock).

Volatility filter: no live VIX feed is available here, so — as the task
brief explicitly allows — this uses a proxy derived from SPY bars: a 20-day
rolling ANNUALIZED realized volatility of SPY's daily log returns.
"Elevated" means being in the top 25% (75th percentile, your confirmed
default) of that volatility proxy's OWN trailing history, computed with an
EXPANDING (not full-sample) percentile rank — a full-sample rank would leak
future volatility into a decision made in the past.

`mean_reversion` runs with the weekly filter AND higher-low gate both
disabled here, same as Task 3 — with either one on, there's no sample to
apply a filter to in the first place. `rsi2_reversion` runs at its real
live settings (no override needed — it already has a real sample).

Run:  python -m microbot.filter_tests
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from .config import settings
from .strategies import RSI2Reversion


def _spy_regime_and_vol(vol_percentile_cutoff: float = 0.75) -> pd.DataFrame:
    """One-time, symbol-independent computation from SPY's own history.
    Returns a frame indexed by date with boolean 'regime_ok' and 'vol_ok'.
    """
    from .data import MarketData
    md = MarketData()
    spy = md.bars("SPY")
    close = spy["close"]
    sma200 = close.rolling(200).mean()
    regime_ok = close > sma200

    log_ret = np.log(close / close.shift(1))
    realized_vol = log_ret.rolling(20).std() * np.sqrt(252)

    # Expanding (causal) percentile rank: at each date, rank realized_vol[t]
    # against every value seen up to AND INCLUDING date t only — no lookahead.
    pct_rank = realized_vol.expanding(min_periods=60).apply(
        lambda x: (x <= x.iloc[-1]).mean(), raw=False)
    vol_ok = pct_rank < vol_percentile_cutoff

    return pd.DataFrame({"regime_ok": regime_ok, "vol_ok": vol_ok}).dropna()


class _ExternalFilterWrapper:
    """Wraps an existing strategy instance and adds an external, date-based
    gate (looked up from a boolean Series) in front of its real evaluate().
    Delegates precompute()/min_bars() unchanged — the wrapped strategy's own
    logic is never modified, just gated."""

    def __init__(self, inner, ok_by_date: pd.Series, name_suffix: str):
        self._inner = inner
        self._ok = ok_by_date
        self.name = inner.name + name_suffix

    def min_bars(self):
        return self._inner.min_bars()

    def precompute(self, df):
        return self._inner.precompute(df)

    def evaluate(self, symbol, df, cache=None):
        d = df.index[-1]
        if not bool(self._ok.get(d, False)):
            return None
        return self._inner.evaluate(symbol, df, cache=cache)


def _make_mean_reversion_no_higher_low():
    from .mean_reversion_variants import _no_higher_low_subclass
    return _no_higher_low_subclass()(weekly_filter=False)


def run_filter_tests(vol_percentile_cutoff: float = 0.75) -> Dict[str, Dict]:
    """Test market-regime filter alone, volatility filter alone, and both
    combined, on top of rsi2_reversion and mean_reversion — plus each
    strategy's own unfiltered baseline for comparison."""
    from .backtest import backtest_symbol
    from .data import MarketData
    from .rebaseline import _combined_universe, strategy_r_stats

    filters = _spy_regime_and_vol(vol_percentile_cutoff)
    both_ok = filters["regime_ok"] & filters["vol_ok"]
    filter_masks = {
        "regime": filters["regime_ok"],
        "vol": filters["vol_ok"],
        "regime_and_vol": both_ok,
    }

    base_strats = {
        "rsi2_reversion": RSI2Reversion,
        "mean_reversion": _make_mean_reversion_no_higher_low,
    }

    all_symbols, dividend_set, ipo_set = _combined_universe()
    non_div = [s for s in all_symbols if s not in dividend_set]
    md = MarketData()

    pooled: Dict[str, List[Dict]] = {}
    for base_name in base_strats:
        pooled[f"{base_name}_unfiltered"] = []
        for filt_name in filter_masks:
            pooled[f"{base_name}_{filt_name}"] = []

    for symbol in non_div:
        lb = settings.ipo_lookback_days if symbol in ipo_set else None
        try:
            df = md.bars(symbol, lookback_days=lb)
        except Exception as e:
            print(f"  ! {symbol}: data error {e}")
            continue
        if df is None or df.empty or len(df) < 60:
            continue

        for base_name, make_strat in base_strats.items():
            pooled[f"{base_name}_unfiltered"].extend(
                backtest_symbol(make_strat(), symbol, df))
            for filt_name, mask in filter_masks.items():
                wrapped = _ExternalFilterWrapper(make_strat(), mask, f"_{filt_name}")
                pooled[f"{base_name}_{filt_name}"].extend(
                    backtest_symbol(wrapped, symbol, df))

    return {name: strategy_r_stats(trades) for name, trades in pooled.items()}


if __name__ == "__main__":
    from .rsi2_variants import print_comparison

    print("Testing market-regime and volatility filters on rsi2_reversion "
          "and mean_reversion against the current universe...\n")
    stats = run_filter_tests()
    print_comparison(stats)
