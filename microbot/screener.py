"""
screener.py
-----------
This is the honest version of "autoresearch to find the correct stocks."

There is no magic that finds risk-free profitable stocks. What we CAN do is, for
every symbol in the universe and every strategy:
  1. Pull history.
  2. Backtest the strategy on that symbol.
  3. Score it on a ROBUSTNESS metric (not just raw return) so we prefer edges
     that show up across many trades, not one lucky streak.
  4. Rank candidates and surface any that have a LIVE signal right now.

Score = expectancy_in_R * sqrt(num_trades), with a profit-factor gate. The
sqrt(trades) term rewards strategies that worked repeatedly; the gate rejects
anything with profit factor < 1.0 (i.e. it lost money in the test).
"""
from __future__ import annotations

import math
from typing import Dict, List

from .backtest import backtest_symbol
from .data import MarketData
from . import metrics
from .strategies import build_default_strategies
from .config import settings


def research(universe: List[str] | None = None, rr: float | None = None,
             min_trades: int = 8) -> Dict:
    universe = universe or settings.universe
    rr = rr or settings.reward_risk_ratio
    md = MarketData()
    strategies = build_default_strategies(rr=rr)

    rankings: List[Dict] = []
    live_signals: List[Dict] = []

    for symbol in universe:
        symbol = symbol.strip().upper()
        try:
            df = md.bars(symbol)
        except Exception as e:
            print(f"  ! {symbol}: data error {e}")
            continue
        if df is None or df.empty or len(df) < 60:
            continue

        for strat in strategies:
            trades = backtest_symbol(strat, symbol, df)
            m = metrics.compute(trades)
            score = 0.0
            if m["trades"] >= min_trades and (m["profit_factor"] or 0) >= 1.0:
                score = round(m["expectancy_r"] * math.sqrt(m["trades"]), 3)
            rankings.append({
                "symbol": symbol, "strategy": strat.name,
                "trades": m["trades"], "win_rate": m["win_rate"],
                "expectancy_r": m["expectancy_r"],
                "profit_factor": m["profit_factor"],
                "max_dd_R": m["max_drawdown"], "score": score,
            })

            # Does this strategy fire on the most recent bar?
            sig = strat.evaluate(symbol, df)
            if sig is not None and score > 0:
                live_signals.append({
                    "symbol": symbol, "strategy": strat.name,
                    "entry": sig.entry, "stop": sig.stop, "target": sig.target,
                    "reason": sig.reason, "score": score,
                })

    rankings.sort(key=lambda r: r["score"], reverse=True)
    live_signals.sort(key=lambda r: r["score"], reverse=True)
    return {"rankings": rankings, "live_signals": live_signals}
