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
from . import ipo_scanner
from .strategies import build_default_strategies, build_dividend_strategies, build_strategies_from_params
from .config import settings


def _scan_symbols(md: MarketData, symbols: List[str], strategies, rr: float,
                  min_trades: int, dividend_set: set,
                  lookback_days: int | None = None,
                  ipo_set: set | None = None) -> tuple[List[Dict], List[Dict]]:
    rankings: List[Dict] = []
    live_signals: List[Dict] = []
    ipo_set = ipo_set or set()
    for symbol in symbols:
        symbol = symbol.strip().upper()
        try:
            lb = lookback_days if symbol in ipo_set else None
            df = md.bars(symbol, lookback_days=lb)
        except Exception as e:
            print(f"  ! {symbol}: data error {e}")
            continue
        if df is None or df.empty or len(df) < 60:
            continue

        is_div = symbol in dividend_set
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
                "dividend": is_div,
            })

            sig = strat.evaluate(symbol, df)
            if sig is not None and score > 0:
                live_signals.append({
                    "symbol": symbol, "strategy": strat.name,
                    "entry": sig.entry, "stop": sig.stop, "target": sig.target,
                    "reason": sig.reason, "score": score,
                    "dividend": is_div,
                })
    return rankings, live_signals


def research(universe: List[str] | None = None, rr: float | None = None,
             min_trades: int = 8,
             default_strats: list | None = None,
             div_strats: list | None = None) -> Dict:
    universe = universe or settings.universe
    rr = rr or settings.reward_risk_ratio
    md = MarketData()

    # Build the combined symbol list, deduplicating while preserving order.
    dividend_set: set = set()
    ipo_set: set = set()
    all_symbols: List[str] = list(universe)
    if settings.include_dividend_stocks:
        for sym in settings.dividend_universe:
            sym = sym.strip().upper()
            dividend_set.add(sym)
            if sym not in all_symbols:
                all_symbols.append(sym)
    if settings.include_split_stocks:
        for sym in settings.split_universe:
            sym = sym.strip().upper()
            if sym not in all_symbols:
                all_symbols.append(sym)
    if settings.include_ipo_stocks:
        # Merge manually configured tickers with EDGAR auto-discovered ones.
        manual = [s.strip().upper() for s in settings.ipo_universe if s.strip()]
        discovered = ipo_scanner.discover_ipos()
        for sym in dict.fromkeys(manual + discovered):  # dedup, preserve order
            ipo_set.add(sym)
            if sym not in all_symbols:
                all_symbols.append(sym)

    # Dividend symbols get the dividend-tuned strategy set; others get the default.
    # Callers (e.g. engine) may inject pre-built strategies from active DB params.
    if default_strats is None:
        default_strats = build_default_strategies(rr=rr)
    if div_strats is None:
        div_strats = build_dividend_strategies(rr=rr)

    non_div = [s for s in all_symbols if s not in dividend_set]
    div_only = [s for s in all_symbols if s in dividend_set]

    r1, s1 = _scan_symbols(md, non_div, default_strats, rr, min_trades, dividend_set,
                           lookback_days=settings.ipo_lookback_days, ipo_set=ipo_set)
    r2, s2 = _scan_symbols(md, div_only, div_strats, rr, min_trades, dividend_set)

    rankings = r1 + r2
    live_signals = s1 + s2

    rankings.sort(key=lambda r: r["score"], reverse=True)
    live_signals.sort(key=lambda r: r["score"], reverse=True)
    return {"rankings": rankings, "live_signals": live_signals,
            "dividend_universe": list(dividend_set),
            "ipo_universe": list(ipo_set)}
