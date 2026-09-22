"""
mean_reversion_variants.py
---------------------------
Task 3: same treatment as rsi2_variants.py, but for the RSI+Bollinger
`mean_reversion` strategy. Tests "exit on strength" (close back above the
middle Bollinger band) against the current fixed bracket exit, with the
same wide-catastrophic-stop and time-stop variants.

Weekly filter override, for THIS BACKTEST ONLY: `mean_reversion`'s live
default has weekly_filter=True, which we found (2026-09-22) makes it fire
ZERO trades across the whole universe — an oversold daily dip and a rising
weekly EMA are nearly mutually exclusive for this setup, the same reason
rsi2_reversion already opts out of the weekly filter in favor of its own
200-day SMA. Every backtest in this module runs with weekly_filter=False so
there are real trades to compare. This is NOT a live change — the actual
MeanReversion() instance the engine trades with still defaults to
weekly_filter=True until you approve turning it off for real.

Live params (confirmed via journal.fetch_active_params(), no optimizer
approval exists for mean_reversion): stop_mult=1.5, rr=2.0 (base Strategy
defaults — MeanReversion doesn't override them, unlike rsi2_reversion).
So the fair "same stop width as the current bracket" comparison here uses
catastrophic_atr_mult=1.5, not the 3.0 used in rsi2_variants.py.

_resolve_exit() is imported from rsi2_variants.py — it's generic ("exit
when close crosses above SOME line") and doesn't care whether that line is
a 5-day SMA or a Bollinger mid-band.

Run:  python -m microbot.mean_reversion_variants
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd

from . import indicators as ind
from .config import settings
from .rsi2_variants import _resolve_exit


def _entry_signal(df: pd.DataFrame, rsi_period=14, rsi_buy=32, bb_period=20,
                   trend_ma=200, atr_period=14,
                   require_higher_low: bool = True) -> pd.DataFrame:
    """Same entry as strategies.MeanReversion.evaluate(), WITHOUT the weekly
    filter (see module docstring) — uptrend, oversold, below the lower
    Bollinger band, and (optionally) a higher low to confirm the dip is
    stabilising.

    require_higher_low=False is ANOTHER backtest-only override (2026-09-22):
    with it on, the uptrend+oversold+below-band+higher-low combo hits only 6
    times across the entire universe/lookback — too thin a sample to compare
    exit rules on at all. Dropping just the higher-low gate (a later bolt-on
    confirmation filter, not part of the core RSI+Bollinger thesis) raises
    the raw candidate count to 64. The live MeanReversion class always
    requires it — this flag only affects this analysis module.
    """
    close = df["close"]
    mid, _, lower = ind.bollinger(close, bb_period)
    rsi = ind.rsi(close, rsi_period)
    trend = ind.sma(close, trend_ma)
    atr = ind.atr(df, atr_period)

    uptrend = close > trend
    oversold = rsi <= rsi_buy
    below_band = close <= lower
    entry = uptrend & oversold & below_band
    if require_higher_low:
        higher_low = df["low"] > df["low"].shift(1)
        entry = entry & higher_low
    return pd.DataFrame({"entry": entry, "atr": atr, "mid_band": mid})


def backtest_mr_variant(df: pd.DataFrame, variant: str,
                         catastrophic_atr_mult: float = 1.5,
                         time_stop_days: Optional[int] = None,
                         rsi_period=14, rsi_buy=32, bb_period=20,
                         trend_ma=200, atr_period=14,
                         require_higher_low: bool = True) -> List[Dict]:
    """variant: "strength" | "strength_cat" | "strength_cat_time"."""
    if variant not in {"strength", "strength_cat", "strength_cat_time"}:
        raise ValueError(f"unknown variant: {variant}")

    min_bars = trend_ma + atr_period + 5
    sig = _entry_signal(df, rsi_period, rsi_buy, bb_period, trend_ma, atr_period,
                         require_higher_low=require_higher_low)
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
            df, i, entry_price, sig["mid_band"], atr_at_entry, variant,
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


def _no_higher_low_subclass():
    """Build a MeanReversion subclass with the higher-low gate removed, on
    demand — the live strategies.MeanReversion hardcodes that check inline
    inside evaluate() rather than exposing it as a constructor flag, so a
    small subclass is the least invasive way to test without it. Test-only:
    never imported or used outside this analysis module."""
    from .strategies import MeanReversion, _bracket

    class _MeanReversionNoHigherLow(MeanReversion):
        def evaluate(self, symbol, df, cache=None):
            if len(df) < self.min_bars():
                return None
            if not self._weekly_aligned(df, cache):
                return None
            close = df["close"]
            d = df.index[-1]
            if cache:
                rsi_now = cache["rsi"].loc[d]
                lower_now = cache["lower_bb"].loc[d]
                trend_now = cache["trend_ma"].loc[d]
                a = cache["atr"].loc[d]
            else:
                rsi_now = ind.rsi(close, self.rsi_period).iloc[-1]
                _, _, lower = ind.bollinger(close, self.bb_period)
                lower_now = lower.iloc[-1]
                trend_now = ind.sma(close, self.trend_ma).iloc[-1]
                a = ind.atr(df, self.atr_period).iloc[-1]

            uptrend = close.iloc[-1] > trend_now
            oversold = rsi_now <= self.rsi_buy
            below_band = close.iloc[-1] <= lower_now
            if uptrend and oversold and below_band:
                why = (f"RSI={rsi_now:.0f}, below lower BB, > MA{self.trend_ma} "
                       f"(higher-low gate disabled for this analysis)")
                return _bracket(symbol, self.name, close.iloc[-1], a,
                                self.stop_mult, self.rr, why)
            return None

    return _MeanReversionNoHigherLow


def backtest_mr_current_bracket(df: pd.DataFrame, rr: float = 2.0,
                                 stop_mult: float = 1.5, rsi_period=14,
                                 rsi_buy=32, bb_period=20, trend_ma=200,
                                 atr_period=14,
                                 require_higher_low: bool = True) -> List[Dict]:
    """The CURRENT live exit (fixed bracket) with the weekly filter disabled
    so it has real trades to compare against — see module docstring. Uses
    the real MeanReversion class + the real backtest_symbol() engine, just
    with weekly_filter=False, so this is exactly what the live strategy
    would do if the filter were removed — not a reimplementation.

    require_higher_low=False additionally drops the higher-low gate, via a
    test-only subclass (see _no_higher_low_subclass) — the live
    MeanReversion class is never modified.
    """
    from .strategies import MeanReversion
    from .backtest import backtest_symbol
    cls = MeanReversion if require_higher_low else _no_higher_low_subclass()
    strat = cls(rsi_period=rsi_period, rsi_buy=rsi_buy,
                bb_period=bb_period, trend_ma=trend_ma,
                atr_period=atr_period, rr=rr, stop_mult=stop_mult,
                weekly_filter=False)
    return backtest_symbol(strat, "?", df)


def run_mr_comparison(catastrophic_atr_mults: Tuple[float, ...] = (6.0, 1.5),
                       time_stop_options: Tuple[int, ...] = (5, 10, 20),
                       require_higher_low: bool = False) -> Dict[str, Dict]:
    from .data import MarketData
    from .rebaseline import _combined_universe, strategy_r_stats

    all_symbols, dividend_set, ipo_set = _combined_universe()
    non_div = [s for s in all_symbols if s not in dividend_set]
    md = MarketData()

    variant_trades: Dict[str, List[Dict]] = {"bracket_no_weekly_filter": []}
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

        variant_trades["bracket_no_weekly_filter"].extend(
            backtest_mr_current_bracket(df, require_higher_low=require_higher_low))
        for mult in catastrophic_atr_mults:
            suffix = f"_{mult:g}x"
            variant_trades[f"strength{suffix}"].extend(
                backtest_mr_variant(df, "strength", mult,
                                    require_higher_low=require_higher_low))
            variant_trades[f"strength_cat{suffix}"].extend(
                backtest_mr_variant(df, "strength_cat", mult,
                                    require_higher_low=require_higher_low))
            for n_days in time_stop_options:
                variant_trades[f"strength_cat_time_{n_days}d{suffix}"].extend(
                    backtest_mr_variant(df, "strength_cat_time", mult,
                                        time_stop_days=n_days,
                                        require_higher_low=require_higher_low))

    return {name: strategy_r_stats(trades) for name, trades in variant_trades.items()}


if __name__ == "__main__":
    from .rsi2_variants import print_comparison

    print("Backtesting mean_reversion exit variants (weekly filter AND "
          "higher-low gate both disabled for this analysis only — see "
          "module docstring) against the current universe...\n")
    stats = run_mr_comparison()
    print_comparison(stats)
