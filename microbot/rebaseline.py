"""
rebaseline.py
-------------
Step 1 of "close the backtest-to-live gap": rerun every strategy's backtest
with the current (fixed) code, using whatever params are actually live right
now, and report honest R-based stats — trade count, win rate, avg winner in
R, avg loser in R, expectancy in R. This is the "what SHOULD these strategies
be doing" baseline we'll compare live results against next.

Run:  python -m microbot.rebaseline
"""
from __future__ import annotations

from math import ceil
from typing import Dict, List, Set, Tuple

from .backtest import backtest_symbol
from .data import MarketData
from . import journal, ipo_scanner
from .strategies import build_strategies_from_params
from .config import settings


def strategy_r_stats(trades: List[Dict], tail_pct: float = 0.05) -> Dict:
    """Pure aggregation: a pool of trade dicts (each with 'r_multiple') ->
    R-based stats. No network, no DB — easy to unit test with made-up trades.

    A trade with r_multiple == 0.0 counts toward the total and the
    expectancy average, but isn't a "win" or a "loss".

    Also reports tail risk, since mean-reversion strategies can look fine on
    win rate/expectancy while hiding rare large losses:
      * worst_trade_r  — the single worst R in the pool.
      * tail_avg_r     — average R of the worst `tail_pct` of trades (bottom
                          5% by default), rounding UP to at least 1 trade.
    """
    if not trades:
        return {"trades": 0, "win_rate": 0.0, "avg_win_r": 0.0,
                "avg_loss_r": 0.0, "expectancy_r": 0.0,
                "worst_trade_r": 0.0, "tail_avg_r": 0.0}

    rs = [t["r_multiple"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    n = len(rs)

    sorted_rs = sorted(rs)  # ascending: worst first
    tail_n = max(1, ceil(n * tail_pct))
    tail = sorted_rs[:tail_n]

    return {
        "trades": n,
        "win_rate": round(len(wins) / n, 4),
        "avg_win_r": round(sum(wins) / len(wins), 3) if wins else 0.0,
        "avg_loss_r": round(sum(losses) / len(losses), 3) if losses else 0.0,
        "expectancy_r": round(sum(rs) / n, 3),
        "worst_trade_r": round(sorted_rs[0], 3),
        "tail_avg_r": round(sum(tail) / len(tail), 3),
    }


def _combined_universe() -> Tuple[List[str], Set[str], Set[str]]:
    """Same universe assembly screener.research() uses: main + dividend +
    split + ipo symbols, deduplicated, with dividend/ipo membership tracked
    so each symbol gets backtested with the right strategy set and lookback."""
    dividend_set: Set[str] = set()
    ipo_set: Set[str] = set()
    all_symbols: List[str] = list(settings.universe)
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
        manual = [s.strip().upper() for s in settings.ipo_universe if s.strip()]
        discovered = ipo_scanner.discover_ipos()
        for sym in dict.fromkeys(manual + discovered):
            ipo_set.add(sym)
            if sym not in all_symbols:
                all_symbols.append(sym)
    return all_symbols, dividend_set, ipo_set


def run_rebaseline(rr: float | None = None) -> Dict[str, Dict]:
    """Backtest every strategy against every symbol in the live universe,
    pool the raw trades PER STRATEGY across all symbols, and return R stats.
    Uses whatever params are currently promoted/live — the same params the
    real engine trades with — so this is an honest "what should be happening"
    baseline, not a re-optimized one.
    """
    rr = rr or settings.reward_risk_ratio
    active = journal.fetch_active_params()
    default_strats = build_strategies_from_params(active, rr=rr)
    div_strats = build_strategies_from_params(active, rr=rr, dividend=True)

    all_symbols, dividend_set, ipo_set = _combined_universe()
    md = MarketData()

    pooled: Dict[str, List[Dict]] = {}
    strat_rr: Dict[str, float] = {}
    for symbol in all_symbols:
        strats = div_strats if symbol in dividend_set else default_strats
        lb = settings.ipo_lookback_days if symbol in ipo_set else None
        try:
            df = md.bars(symbol, lookback_days=lb)
        except Exception as e:
            print(f"  ! {symbol}: data error {e}")
            continue
        if df is None or df.empty or len(df) < 60:
            continue
        for strat in strats:
            strat_rr[strat.name] = strat.rr
            trades = backtest_symbol(strat, symbol, df)
            pooled.setdefault(strat.name, []).extend(trades)

    report = {}
    for name, trades in pooled.items():
        s = strategy_r_stats(trades)
        rr_used = strat_rr.get(name)
        # Breakeven win rate at this strategy's reward:risk — the win rate
        # needed just to break even, before counting a single R of edge.
        s["breakeven_win_rate"] = round(1 / (1 + rr_used), 4) if rr_used else None
        report[name] = s
    return report


def print_report(stats: Dict[str, Dict]) -> None:
    print(f"{'Strategy':<20} {'N':>5} {'Win%':>7} {'BE-WR%':>7} {'AvgWinR':>9} "
          f"{'AvgLossR':>10} {'ExpR':>8} {'WorstR':>8} {'Tail5%R':>8}")
    print("-" * 96)
    for name in sorted(stats, key=lambda k: stats[k]["expectancy_r"], reverse=True):
        s = stats[name]
        be = f"{s['breakeven_win_rate'] * 100:>6.1f}%" if s["breakeven_win_rate"] else "    n/a"
        print(f"{name:<20} {s['trades']:>5} {s['win_rate'] * 100:>6.1f}% {be} "
              f"{s['avg_win_r']:>+9.3f} {s['avg_loss_r']:>+10.3f} {s['expectancy_r']:>+8.3f} "
              f"{s['worst_trade_r']:>+8.3f} {s['tail_avg_r']:>+8.3f}")


if __name__ == "__main__":
    print("Rebaselining every live strategy against the current universe "
          "(this refetches bars for every symbol, so it takes a few minutes)...\n")
    stats = run_rebaseline()
    print_report(stats)
