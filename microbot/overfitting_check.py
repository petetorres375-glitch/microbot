"""
overfitting_check.py
---------------------
Task 5: sanity-check that rsi2_reversion's backtest edge (+0.260R over 317
trades, from rebaseline.py — the one strategy that's looked good throughout
Tasks 1-4) isn't a full-sample overfit artifact.

Two checks:
  1. Chronological in-sample / out-of-sample split — trades are bucketed by
     entry index using the same 75/25 split this project's own optimizer.py
     already uses (IS_SPLIT = 0.75), so results are directly comparable to
     how the rest of the codebase already talks about IS/OOS. Indicators are
     computed on the FULL history as usual (they're already causal — see
     backtest_symbol()'s own docstring/history) and trades are only split
     into buckets AFTER backtesting, by entry_idx / total_bars. Re-running
     the walk-forward on a truncated OOS-only slice instead would starve a
     200+ bar-lookback strategy like this one of its own warmup period.
  2. Parameter sensitivity sweep around the live rsi_buy=10, stop_mult=3.0 —
     if nearby values collapse to a much worse or negative expectancy,
     that's a sign the current numbers are a narrow, likely-overfit spike
     rather than a real, robust effect.

Run:  python -m microbot.overfitting_check
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from .backtest import backtest_symbol
from .strategies import RSI2Reversion

IS_SPLIT = 0.75  # matches microbot/optimizer.py's own IS_SPLIT


def split_trades_by_period(trades: List[Dict], total_bars: int,
                            is_split: float = IS_SPLIT) -> Tuple[List[Dict], List[Dict]]:
    """Pure bucketing: given a symbol's full-history trade list and its bar
    count, split trades into in-sample (entered before the cutoff) and
    out-of-sample (entered at or after it), by entry_idx. No network, no
    backtesting — easy to unit test with made-up trades."""
    cutoff = int(total_bars * is_split)
    is_trades = [t for t in trades if t["entry_idx"] < cutoff]
    oos_trades = [t for t in trades if t["entry_idx"] >= cutoff]
    return is_trades, oos_trades


def _fetch_universe_bars() -> Dict[str, object]:
    """Fetch bars for every non-dividend symbol once, so both checks below
    can reuse the same data instead of hitting the network twice."""
    from .data import MarketData
    from .rebaseline import _combined_universe
    from .config import settings

    all_symbols, dividend_set, ipo_set = _combined_universe()
    non_div = [s for s in all_symbols if s not in dividend_set]
    md = MarketData()

    dfs = {}
    for symbol in non_div:
        lb = settings.ipo_lookback_days if symbol in ipo_set else None
        try:
            df = md.bars(symbol, lookback_days=lb)
        except Exception as e:
            print(f"  ! {symbol}: data error {e}")
            continue
        if df is None or df.empty or len(df) < 60:
            continue
        dfs[symbol] = df
    return dfs


def run_is_oos_check(dfs: Dict[str, object], strat=None,
                      is_split: float = IS_SPLIT) -> Dict[str, Dict]:
    """Backtest the given strategy (rsi2_reversion by default) across the
    given symbols, then bucket the resulting trades into in-sample vs
    out-of-sample. `strat` can be any strategy-shaped object — including a
    filter-wrapped one from filter_tests.py/momentum_filter_tests.py — so
    this same IS/OOS check can validate any "recommended change", not just
    rsi2_reversion's own edge, per Task 5's "every recommended change"."""
    from .rebaseline import strategy_r_stats

    if strat is None:
        strat = RSI2Reversion()
    is_pool: List[Dict] = []
    oos_pool: List[Dict] = []
    for symbol, df in dfs.items():
        trades = backtest_symbol(strat, symbol, df)
        is_trades, oos_trades = split_trades_by_period(trades, len(df), is_split)
        is_pool.extend(is_trades)
        oos_pool.extend(oos_trades)

    return {"in_sample": strategy_r_stats(is_pool),
            "out_of_sample": strategy_r_stats(oos_pool)}


def run_param_sensitivity(dfs: Dict[str, object],
                           rsi_buy_values: Tuple[int, ...] = (8, 10, 12),
                           stop_mult_values: Tuple[float, ...] = (2.5, 3.0, 3.5)
                           ) -> Dict[str, Dict]:
    """Backtest rsi2_reversion across a small grid around its live params to
    check whether nearby values also look reasonable, or whether the live
    combo (rsi_buy=10, stop_mult=3.0) is a narrow, likely-overfit spike."""
    from .rebaseline import strategy_r_stats

    results: Dict[str, Dict] = {}
    for rsi_buy in rsi_buy_values:
        for stop_mult in stop_mult_values:
            strat = RSI2Reversion(rsi_buy=rsi_buy, stop_mult=stop_mult)
            pool: List[Dict] = []
            for symbol, df in dfs.items():
                pool.extend(backtest_symbol(strat, symbol, df))
            label = f"rsi_buy={rsi_buy}_stop_mult={stop_mult:g}"
            results[label] = strategy_r_stats(pool)
    return results


if __name__ == "__main__":
    from .rsi2_variants import print_comparison

    print("Fetching universe bars once for both checks "
          "(this takes a few minutes)...\n")
    dfs = _fetch_universe_bars()

    print("Task 5a: in-sample vs out-of-sample split "
          f"({IS_SPLIT:.0%}/{1 - IS_SPLIT:.0%}, entry-index bucketed)...\n")
    print_comparison(run_is_oos_check(dfs))

    print("\nTask 5b: parameter sensitivity sweep around "
          "rsi_buy=10, stop_mult=3.0...\n")
    print_comparison(run_param_sensitivity(dfs))
