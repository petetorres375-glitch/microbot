"""
momentum_filter_tests.py
-------------------------
Task 6: test filters on trend_momentum, breakout, and breakout_52w —
strictly filters, per your instruction not to chase win rate on these by
adding earlier profit-taking or tighter trailing stops. Judged on
R-expectancy alone, same as everywhere else in this plan.

Reuses Task 4's market-regime and volatility filters unchanged
(_spy_regime_and_vol, _ExternalFilterWrapper from filter_tests.py) and adds
one new one that only makes sense for these strategies: a PER-SYMBOL trend
filter (that stock's own close > its own 200-day SMA), since — unlike
rsi2_reversion/mean_reversion — none of these three strategies already
require this. breakout_52w's entry (a 200-day high) very likely already
implies it in practice; breakout's entry (a 20-day high) does not — a stock
can make a short-term high while still under its 200-day SMA.

Each strategy runs with its real LIVE promoted params, via the exact same
build_strategies_from_params() the engine itself uses — so if the optimizer
has approved different parameters since this was written, this test
automatically picks them up rather than silently testing stale defaults.

Run:  python -m microbot.momentum_filter_tests
"""
from __future__ import annotations

from typing import Dict, List

from . import indicators as ind
from .filter_tests import _ExternalFilterWrapper, _spy_regime_and_vol


class _PerSymbolTrendFilterWrapper:
    """Adds a per-symbol trend filter (close > that symbol's OWN 200-day
    SMA) in front of an existing strategy's evaluate(). Unlike
    _ExternalFilterWrapper's market-wide SPY-derived gate, this is computed
    per symbol from the same df the wrapped strategy already sees."""

    def __init__(self, inner, trend_ma: int = 200, name_suffix: str = "_trend200"):
        self._inner = inner
        self.trend_ma = trend_ma
        self.name = inner.name + name_suffix

    def min_bars(self):
        return max(self._inner.min_bars(), self.trend_ma + 5)

    def precompute(self, df):
        cache = dict(self._inner.precompute(df) or {})
        cache["_trend200"] = ind.sma(df["close"], self.trend_ma)
        return cache

    def evaluate(self, symbol, df, cache=None):
        d = df.index[-1]
        if cache and "_trend200" in cache:
            sma_now = cache["_trend200"].loc[d]
        else:
            sma_now = ind.sma(df["close"], self.trend_ma).iloc[-1]
        if df["close"].iloc[-1] <= sma_now:
            return None
        return self._inner.evaluate(symbol, df, cache=cache)


def _make_live_strategies():
    """Build trend_momentum, breakout, and breakout_52w with whatever
    params are currently promoted/live — same mechanism engine.py uses."""
    from . import journal
    from .strategies import build_strategies_from_params
    from .config import settings

    active = journal.fetch_active_params()
    strats = build_strategies_from_params(active, rr=settings.reward_risk_ratio)
    by_name = {s.name: s for s in strats}
    return {name: by_name[name] for name in ("trend_momentum", "breakout", "breakout_52w")
            if name in by_name}


def run_momentum_filter_tests(vol_percentile_cutoff: float = 0.75) -> Dict[str, Dict]:
    """Test the per-symbol trend filter, market-regime filter, volatility
    filter, and all three combined, on top of each momentum strategy's own
    unfiltered live baseline."""
    from .backtest import backtest_symbol
    from .data import MarketData
    from .rebaseline import _combined_universe, strategy_r_stats
    from .config import settings

    filters = _spy_regime_and_vol(vol_percentile_cutoff)

    base_strats = _make_live_strategies()
    all_symbols, dividend_set, ipo_set = _combined_universe()
    non_div = [s for s in all_symbols if s not in dividend_set]
    md = MarketData()

    pooled: Dict[str, List[Dict]] = {}
    variant_names = ["unfiltered", "trend200", "regime", "vol", "all_three"]
    for base_name in base_strats:
        for v in variant_names:
            pooled[f"{base_name}_{v}"] = []

    for symbol in non_div:
        lb = settings.ipo_lookback_days if symbol in ipo_set else None
        try:
            df = md.bars(symbol, lookback_days=lb)
        except Exception as e:
            print(f"  ! {symbol}: data error {e}")
            continue
        if df is None or df.empty or len(df) < 60:
            continue

        for base_name, strat in base_strats.items():
            pooled[f"{base_name}_unfiltered"].extend(
                backtest_symbol(strat, symbol, df))

            trend_wrapped = _PerSymbolTrendFilterWrapper(strat)
            pooled[f"{base_name}_trend200"].extend(
                backtest_symbol(trend_wrapped, symbol, df))

            regime_wrapped = _ExternalFilterWrapper(strat, filters["regime_ok"], "_regime")
            pooled[f"{base_name}_regime"].extend(
                backtest_symbol(regime_wrapped, symbol, df))

            vol_wrapped = _ExternalFilterWrapper(strat, filters["vol_ok"], "_vol")
            pooled[f"{base_name}_vol"].extend(
                backtest_symbol(vol_wrapped, symbol, df))

            both_ok = filters["regime_ok"] & filters["vol_ok"]
            all_three = _PerSymbolTrendFilterWrapper(
                _ExternalFilterWrapper(strat, both_ok, "_regime_vol"))
            pooled[f"{base_name}_all_three"].extend(
                backtest_symbol(all_three, symbol, df))

    return {name: strategy_r_stats(trades) for name, trades in pooled.items()}


if __name__ == "__main__":
    from .rsi2_variants import print_comparison

    print("Testing per-symbol trend, market-regime, and volatility filters "
          "on trend_momentum, breakout, and breakout_52w "
          "(live promoted params) against the current universe...\n")
    stats = run_momentum_filter_tests()
    print_comparison(stats)
