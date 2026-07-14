"""
backtest.py
-----------
A simple, honest bar-by-bar backtester. For each historical bar it asks the
strategy for a signal, and if flat, "enters" a trade with the same bracket
(stop + 2:1 target) the live bot would use. Then it walks forward bar by bar
until either the stop or the target is hit, records the result in R-multiples,
and moves on.

Deliberate simplifications (so you trust the numbers and know their limits):
  * One open position per symbol at a time.
  * Fills assumed at the signal bar's close; stop/target checked on later bars'
    high/low. If a single bar spans both, we assume the STOP fills first
    (conservative / pessimistic — real life is messy).
  * Overnight gaps modeled: if a bar opens below the stop, the fill is at the
    open price (not at the stop), correctly reflecting gap-through slippage.
  * No commission model (Alpaca is commission-free), but slippage cents can be
    passed in.
This is good enough to RANK strategies and reject bad ones. It is NOT a promise
of live results.
"""
from __future__ import annotations

from typing import List, Dict
import pandas as pd

from .strategies import Strategy


def backtest_symbol(strategy: Strategy, symbol: str, df: pd.DataFrame,
                    slippage: float = 0.0) -> List[Dict]:
    trades: List[Dict] = []
    i = strategy.min_bars()
    n = len(df)
    # Precompute every indicator ONCE on the full history (see
    # Strategy.precompute docstring) instead of recomputing on a truncated
    # window at every single bar - this is what made research() take 10+
    # minutes across the full universe.
    cache = strategy.precompute(df)
    while i < n:
        window = df.iloc[: i + 1]
        sig = strategy.evaluate(symbol, window, cache=cache)
        if sig is None:
            i += 1
            continue

        entry = sig.entry + slippage
        risk = entry - sig.stop
        if risk <= 0:
            i += 1
            continue

        # Walk forward to resolve the trade.
        exit_price, exit_idx, outcome = None, None, None
        for j in range(i + 1, n):
            o = df["open"].iloc[j]
            hi, lo = df["high"].iloc[j], df["low"].iloc[j]
            if o <= sig.stop:
                # Overnight gap blows through stop — fill at open, not stop price.
                exit_price, outcome = o, "stop"
            else:
                hit_stop = lo <= sig.stop
                hit_target = hi >= sig.target
                if hit_stop and hit_target:    # ambiguous bar -> assume stop
                    exit_price, outcome = sig.stop, "stop"
                elif hit_stop:
                    exit_price, outcome = sig.stop, "stop"
                elif hit_target:
                    exit_price, outcome = sig.target, "target"
            if outcome:
                exit_idx = j
                break

        if outcome is None:  # ran out of data still open -> close at last bar
            exit_price = df["close"].iloc[-1]
            exit_idx = n - 1
            outcome = "open_end"

        pnl_per_share = exit_price - entry
        trades.append({
            "symbol": symbol, "strategy": strategy.name,
            "entry_idx": i, "exit_idx": exit_idx,
            "entry": round(entry, 2), "exit": round(exit_price, 2),
            "stop": sig.stop, "target": sig.target,
            "outcome": outcome,
            "pnl": round(pnl_per_share, 4),               # per-share $
            "r_multiple": round(pnl_per_share / risk, 3),  # R units
        })
        i = exit_idx + 1  # no overlapping trades
    return trades
