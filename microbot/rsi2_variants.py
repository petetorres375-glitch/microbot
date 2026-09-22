"""
rsi2_variants.py
----------------
Task 2 of the win-rate/R-expectancy plan: compare our current rsi2_reversion
exit (a fixed 1:1 bracket) against Larry Connors' actual published exit rule
("hold until the close moves back above the 5-day SMA, no fixed stop" —
verified against StockCharts ChartSchool's RSI(2) page, 2026-09-22), plus two
risk-managed variants of that published rule.

Entry is IDENTICAL across every variant (only the exit differs), matching
Connors' published entry (RSI(2) <= rsi_buy, close > 200-day SMA) plus this
bot's existing extra confirmation (close below the 5-day SMA) — see
strategies.RSI2Reversion for the citation and rationale.

Variants:
  "bracket"            - our current live exit. NOT reimplemented here — see
                         rebaseline.py's rsi2_reversion row for its numbers.
  "strength"           - Connors' real rule: exit on close > 5-day SMA, no stop.
  "strength_cat"       - "strength" + a wide catastrophic stop (default 6x ATR)
                         as a rare backstop, not the primary exit.
  "strength_cat_time"  - "strength_cat" + a time stop: force-exit at N days if
                         neither other exit has fired yet.

R-multiple convention: for all three variants here, R is measured against the
SAME notional risk unit — catastrophic_atr_mult * ATR at entry — even for
"strength" (which enforces no real stop). This makes the three comparable to
each other on an apples-to-apples R scale, and makes "strength"'s tail risk
visible instead of undefined (Connors used no stop, so without this
convention its R-multiple would have no denominator at all). It is NOT the
same R denominator as "bracket" (which uses its own 3x ATR stop) — compare
win rate / outcome shape between the two families more than raw R side by side.

Run:  python -m microbot.rsi2_variants
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd

from . import indicators as ind
from .config import settings


def _entry_signal(df: pd.DataFrame, rsi_period=2, rsi_buy=10,
                   trend_ma=200, stretch_ma=5, atr_period=14) -> pd.DataFrame:
    """Precompute every indicator once. Returns a frame indexed like df with
    a boolean 'entry' column and the ATR at each bar (for stop sizing) — same
    entry condition as strategies.RSI2Reversion.evaluate()."""
    close = df["close"]
    rsi = ind.rsi(close, rsi_period)
    long_trend = ind.sma(close, trend_ma)
    short_ma = ind.sma(close, stretch_ma)
    atr = ind.atr(df, atr_period)
    entry = (close > long_trend) & (rsi <= rsi_buy) & (close < short_ma)
    return pd.DataFrame({"entry": entry, "atr": atr, "short_ma": short_ma})


def _resolve_exit(df: pd.DataFrame, entry_idx: int, entry_price: float,
                   short_ma: pd.Series, atr_at_entry: float, variant: str,
                   catastrophic_atr_mult: float,
                   time_stop_days: Optional[int]) -> Tuple[float, int, str]:
    """Pure walk-forward exit resolution starting the bar AFTER entry_idx.
    No entry detection here — easy to unit test with a hand-built price path.

    Priority within a bar (matches the conservative convention used
    elsewhere in this codebase): a catastrophic stop touch/gap beats a
    same-day strength exit, which beats a same-day time stop.
    """
    notional_risk = catastrophic_atr_mult * atr_at_entry
    catastrophic_stop = entry_price - notional_risk
    use_stop = variant in {"strength_cat", "strength_cat_time"}
    use_time_stop = variant == "strength_cat_time"
    n = len(df)

    for j in range(entry_idx + 1, n):
        days_held = j - entry_idx
        o = df["open"].iloc[j]
        lo = df["low"].iloc[j]
        c = df["close"].iloc[j]

        if use_stop:
            if o <= catastrophic_stop:  # overnight gap through the stop
                return o, j, "catastrophic_stop"
            if lo <= catastrophic_stop:
                return catastrophic_stop, j, "catastrophic_stop"
        if c > short_ma.iloc[j]:
            return c, j, "strength"
        if use_time_stop and days_held >= time_stop_days:
            return c, j, "time_stop"

    return df["close"].iloc[-1], n - 1, "open_end"


def backtest_rsi2_variant(df: pd.DataFrame, variant: str,
                           catastrophic_atr_mult: float = 6.0,
                           time_stop_days: Optional[int] = None,
                           rsi_period=2, rsi_buy=10, trend_ma=200,
                           stretch_ma=5, atr_period=14) -> List[Dict]:
    """Walk-forward backtest of one Connors exit variant across one symbol's
    full history. variant: "strength" | "strength_cat" | "strength_cat_time".
    """
    if variant not in {"strength", "strength_cat", "strength_cat_time"}:
        raise ValueError(f"unknown variant: {variant}")

    min_bars = trend_ma + atr_period + 5
    sig = _entry_signal(df, rsi_period, rsi_buy, trend_ma, stretch_ma, atr_period)
    close = df["close"]
    n = len(df)

    trades: List[Dict] = []
    i = min_bars
    while i < n:
        if not sig["entry"].iloc[i]:
            i += 1
            continue

        entry_price = close.iloc[i]
        atr_at_entry = sig["atr"].iloc[i]
        notional_risk = catastrophic_atr_mult * atr_at_entry

        exit_price, exit_idx, outcome = _resolve_exit(
            df, i, entry_price, sig["short_ma"], atr_at_entry, variant,
            catastrophic_atr_mult, time_stop_days)

        pnl_per_share = exit_price - entry_price
        trades.append({
            "entry_idx": i, "exit_idx": exit_idx,
            "entry": round(entry_price, 2), "exit": round(exit_price, 2),
            "outcome": outcome,
            "pnl": round(pnl_per_share, 4),
            "r_multiple": round(pnl_per_share / notional_risk, 3),
        })
        i = exit_idx + 1  # no overlapping trades

    return trades


def run_rsi2_comparison(catastrophic_atr_mults: Tuple[float, ...] = (6.0, 3.0),
                         time_stop_options: Tuple[int, ...] = (5, 10, 20)) -> Dict[str, Dict]:
    """Backtest every Connors exit variant against every non-dividend symbol
    in the live universe (rsi2_reversion isn't part of the dividend strategy
    set), pool trades per variant, and return R stats.

    catastrophic_atr_mults is plural and tested at each width given: the
    R-multiple denominator changes with stop width, so "strength" (no real
    stop) and "strength_cat"/"strength_cat_time" (a real stop) both need to
    be compared at the SAME width to be apples-to-apples — 3.0 matches the
    current live bracket's stop exactly, so that's the fair comparison;
    6.0 is kept so the earlier ("does a stop even matter") reading still
    has its own row.
    """
    from .data import MarketData
    from .rebaseline import _combined_universe, strategy_r_stats

    all_symbols, dividend_set, ipo_set = _combined_universe()
    non_div = [s for s in all_symbols if s not in dividend_set]
    md = MarketData()

    variant_trades: Dict[str, List[Dict]] = {}
    for mult in catastrophic_atr_mults:
        suffix = f"_{mult:g}x"
        variant_trades[f"strength{suffix}"] = []
        variant_trades[f"strength_cat{suffix}"] = []
        for n_days in time_stop_options:
            variant_trades[f"strength_cat_time_{n_days}d{suffix}"] = []

    for symbol in non_div:
        lb = settings.ipo_lookback_days if symbol in ipo_set else None
        try:
            df = md.bars(symbol, lookback_days=lb)
        except Exception as e:
            print(f"  ! {symbol}: data error {e}")
            continue
        if df is None or df.empty or len(df) < 60:
            continue

        for mult in catastrophic_atr_mults:
            suffix = f"_{mult:g}x"
            variant_trades[f"strength{suffix}"].extend(
                backtest_rsi2_variant(df, "strength", mult))
            variant_trades[f"strength_cat{suffix}"].extend(
                backtest_rsi2_variant(df, "strength_cat", mult))
            for n_days in time_stop_options:
                variant_trades[f"strength_cat_time_{n_days}d{suffix}"].extend(
                    backtest_rsi2_variant(df, "strength_cat_time", mult,
                                          time_stop_days=n_days))

    return {name: strategy_r_stats(trades) for name, trades in variant_trades.items()}


def print_comparison(stats: Dict[str, Dict]) -> None:
    # Size the name column to the longest variant name actually present,
    # so long names (e.g. "rsi2_reversion_regime_and_vol") can't push every
    # column after them out of alignment.
    name_width = max([len("Variant")] + [len(n) for n in stats]) + 1
    header = (f"{'Variant':<{name_width}} {'N':>5} {'Win%':>7} {'AvgWinR':>9} "
              f"{'AvgLossR':>10} {'ExpR':>8} {'WorstR':>8} {'Tail5%R':>8}")
    print(header)
    print("-" * len(header))
    for name in sorted(stats, key=lambda k: stats[k]["expectancy_r"], reverse=True):
        s = stats[name]
        print(f"{name:<{name_width}} {s['trades']:>5} {s['win_rate'] * 100:>6.1f}% "
              f"{s['avg_win_r']:>+9.3f} {s['avg_loss_r']:>+10.3f} {s['expectancy_r']:>+8.3f} "
              f"{s['worst_trade_r']:>+8.3f} {s['tail_avg_r']:>+8.3f}")


if __name__ == "__main__":
    print("Backtesting Connors RSI(2) exit variants against the current universe "
          "(this refetches bars for every symbol, so it takes a few minutes)...\n")
    stats = run_rsi2_comparison()
    print_comparison(stats)
    print("\n(For the current live bracket exit, see the 'rsi2_reversion' row from "
          "`python -m microbot.rebaseline` — its R uses a different denominator, "
          "a 3x-ATR stop vs this module's 6x-ATR notional risk, so compare win rate "
          "and outcome shape more than raw R side by side.)")
