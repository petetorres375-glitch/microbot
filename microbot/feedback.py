"""
feedback.py
-----------
The adaptive loop: "use the closed-trade history to make the bot better."

It uses the native analyzer's expectancy / win_rate helpers on the bot's journal
to find strategy (and strategy+symbol) combos that are losing money over a real
sample, and returns a veto set so the engine stops taking them.

Guardrail: we only veto once a combo has a MINIMUM number of closed trades
(default 6). Vetoing on 1-2 trades is noise-chasing and makes the bot worse.

Expectancy is judged in R, not dollars. Dollar P&L scales with STARTING_EQUITY,
so averaging dollars across a sizing change (e.g. $5k -> $50k on 2026-08-28)
lets one post-change loss outweigh ~10 earlier wins. R is size-independent.

A negative average isn't enough on its own: a 1:1 strategy like rsi2_reversion
with a real 60% win rate still shows avg R <= 0 after 6 trades ~46% of the time.
So we only veto when the losing record is clear — average R plus `z` standard
errors is still below `min_expectancy`.

Only trades ENTERED under a strategy's current params count: once the optimizer
promotes new params (active_params.promoted_ts), the old version's record no
longer says anything about the strategy that's actually running. Entry time
comes from the matching order row (trades only store the close time), so a
position opened before a retune and closed after it is still excluded.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Set, Tuple

import pandas as pd

from . import analyzer, journal


@dataclass
class FeedbackConfig:
    min_trades: int = 6            # need a real sample before judging
    min_expectancy: float = 0.0    # veto combos clearly below this (R)
    z: float = 1.0                 # standard errors of margin (~84% one-sided)


def _clearly_losing(g: pd.DataFrame, cfg: FeedbackConfig) -> bool:
    """True when avg R + z * standard error is still below min_expectancy."""
    r = g["r_multiple"].dropna()
    if len(r) < 2:
        return False
    se = float(r.std(ddof=1)) / len(r) ** 0.5
    return float(r.mean()) + cfg.z * se < cfg.min_expectancy


def _entered_under_current_params(df: pd.DataFrame) -> pd.DataFrame:
    """Drop trades entered before their strategy's current params were promoted.
    Strategies that were never promoted keep their full history."""
    promoted = {k: pd.Timestamp(v) for k, v in journal.fetch_promoted_dates().items()}
    if not promoted:
        return df
    orders = pd.DataFrame(journal.fetch_orders(limit=1_000_000))
    if not orders.empty:
        orders["entered"] = pd.to_datetime(orders["ts"], format="mixed", utc=True)
    closed = pd.to_datetime(df["ts"], format="mixed", utc=True)

    keep = []
    for i, row in df.iterrows():
        since = promoted.get(row["strategy"])
        if since is None:
            keep.append(True)
            continue
        entered = closed[i]  # fallback if no matching order row exists
        if not orders.empty:
            m = orders[(orders["symbol"] == row["symbol"])
                       & (orders["strategy"] == row["strategy"])
                       & (orders["entered"] <= closed[i])]
            if not m.empty:
                entered = m["entered"].max()
        keep.append(entered >= since)
    return df[keep]


def compute_vetoes(cfg: FeedbackConfig = FeedbackConfig()) -> dict:
    """Return {'setups': set, 'symbols': set, 'combos': set, 'table': df}."""
    # analyzer.frame() already excludes zero-P&L reconciliation artifacts,
    # so min_trades counts real fills only.
    df = analyzer.frame()
    if not df.empty:
        df = _entered_under_current_params(df)
    if df.empty:
        return {"setups": set(), "symbols": set(), "combos": set(),
                "table": pd.DataFrame()}

    bad_setups: Set[str] = set()
    bad_symbols: Set[str] = set()
    bad_combos: Set[Tuple[str, str]] = set()
    rows = []

    # Per strategy
    for strategy, g in df.groupby("strategy"):
        if len(g) >= cfg.min_trades:
            exp = analyzer.expectancy_r(g)
            rows.append({"level": "strategy", "key": strategy, "trades": len(g),
                         "win_rate": round(analyzer.win_rate(g), 1),
                         "expectancy_r": round(exp, 2),
                         "expectancy": round(analyzer.expectancy(g), 2)})
            if _clearly_losing(g, cfg):
                bad_setups.add(strategy)

    # Per strategy+symbol combo (finer grained)
    for (strategy, symbol), g in df.groupby(["strategy", "symbol"]):
        if len(g) >= cfg.min_trades and _clearly_losing(g, cfg):
            bad_combos.add((strategy, symbol))

    return {"setups": bad_setups, "symbols": bad_symbols,
            "combos": bad_combos, "table": pd.DataFrame(rows)}


def allow_signal(signal_row: dict, vetoes: dict) -> bool:
    """True = take the trade, False = veto. Used by engine.apply_trade_analyzer."""
    setup = signal_row.get("strategy")
    symbol = signal_row.get("symbol")
    if setup in vetoes.get("setups", set()):
        return False
    if (setup, symbol) in vetoes.get("combos", set()):
        return False
    return True
