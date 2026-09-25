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
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Set, Tuple

import pandas as pd

from . import analyzer


@dataclass
class FeedbackConfig:
    min_trades: int = 6            # need a real sample before judging
    min_expectancy: float = 0.0    # veto combos with expectancy <= this (R)


def compute_vetoes(cfg: FeedbackConfig = FeedbackConfig()) -> dict:
    """Return {'setups': set, 'symbols': set, 'combos': set, 'table': df}."""
    # analyzer.frame() already excludes zero-P&L reconciliation artifacts,
    # so min_trades counts real fills only.
    df = analyzer.frame()
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
            if exp <= cfg.min_expectancy:
                bad_setups.add(strategy)

    # Per strategy+symbol combo (finer grained)
    for (strategy, symbol), g in df.groupby(["strategy", "symbol"]):
        if len(g) >= cfg.min_trades and analyzer.expectancy_r(g) <= cfg.min_expectancy:
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
