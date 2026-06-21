"""
performance.py
--------------
Closed-trade performance summary pulled from the journal DB.

Run:  python -m microbot.performance
      python -m microbot.performance --db path/to/microbot.db
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from typing import Dict, List


def _load_swing_trades(db_path: str) -> List[Dict]:
    """Load real closed swing trades, excluding no-fills and zero-P&L manual records."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT symbol, strategy, qty, entry, exit, outcome, pnl, r_multiple, ts"
        " FROM trades ORDER BY ts"
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        if d["outcome"] == "no_fill":
            continue
        if d["outcome"] == "manual" and d["pnl"] == 0.0:
            continue
        result.append(d)
    return result


def _load_intraday_trades(db_path: str) -> List[Dict]:
    """Load closed intraday (ORB) trades."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT symbol, strategy, qty, entry, stop, target, exit_price,"
        " exit_reason, pnl, r_multiple, date"
        " FROM intraday_trades WHERE status='closed' ORDER BY ts_open"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def compute_metrics(trades: List[Dict]) -> Dict:
    """Compute summary stats from a list of trade dicts (must have pnl, r_multiple, symbol)."""
    if not trades:
        return {"n": 0}
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    win_rate = len(wins) / n * 100
    total_pnl = sum(t["pnl"] for t in trades)
    r_vals = [t["r_multiple"] for t in trades]
    expectancy = sum(r_vals) / n
    avg_win_r = sum(t["r_multiple"] for t in wins) / len(wins) if wins else 0.0
    avg_loss_r = sum(t["r_multiple"] for t in losses) / len(losses) if losses else 0.0
    best = max(trades, key=lambda t: t["pnl"])
    worst = min(trades, key=lambda t: t["pnl"])

    max_consec = cur_consec = 0
    for t in trades:
        if t["pnl"] < 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0

    return {
        "n": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "expectancy_r": expectancy,
        "avg_win_r": avg_win_r,
        "avg_loss_r": avg_loss_r,
        "best_pnl": best["pnl"],
        "best_r": best["r_multiple"],
        "best_sym": best["symbol"],
        "worst_pnl": worst["pnl"],
        "worst_r": worst["r_multiple"],
        "worst_sym": worst["symbol"],
        "max_consec_loss": max_consec,
    }


def _fmt_r(r: float) -> str:
    sign = "+" if r >= 0 else ""
    return f"{sign}{r:.2f}R"


def _fmt_pnl(pnl: float) -> str:
    sign = "+" if pnl >= 0 else ""
    return f"{sign}${pnl:.2f}"


def _print_section(m: Dict, title: str):
    pad = "  "
    print(f"\n{title}")
    print(f"{pad}{'-' * 40}")
    if m["n"] == 0:
        print(f"{pad}No closed trades.")
        return
    n = m["n"]
    note = "  (< 10 trades — stats not yet reliable)" if n < 10 else ""
    print(f"{pad}Trades       {n}{note}")
    print(f"{pad}Win rate     {m['win_rate']:.0f}%  ({m['wins']}W / {m['losses']}L)")
    print(f"{pad}Expectancy   {_fmt_r(m['expectancy_r'])}")
    print(f"{pad}Avg winner   {_fmt_r(m['avg_win_r'])}")
    print(f"{pad}Avg loser    {_fmt_r(m['avg_loss_r'])}")
    print(f"{pad}Total P&L    {_fmt_pnl(m['total_pnl'])}")
    print(f"{pad}Best trade   {_fmt_pnl(m['best_pnl'])} ({_fmt_r(m['best_r'])})  [{m['best_sym']}]")
    print(f"{pad}Worst trade  {_fmt_pnl(m['worst_pnl'])} ({_fmt_r(m['worst_r'])})  [{m['worst_sym']}]")
    print(f"{pad}Max consec L {m['max_consec_loss']}")


def _print_strategy_breakdown(trades: List[Dict]):
    if not trades:
        return
    by_strat: Dict[str, list] = defaultdict(list)
    for t in trades:
        by_strat[t["strategy"]].append(t)
    print(f"\n  {'Strategy':<22} {'N':>4} {'Win%':>6} {'Exp R':>7} {'P&L':>10}")
    print(f"  {'─' * 22} {'─' * 4} {'─' * 6} {'─' * 7} {'─' * 10}")
    for strat in sorted(by_strat):
        m = compute_metrics(by_strat[strat])
        note = "*" if m["n"] < 5 else " "
        print(f"  {strat:<22} {m['n']:>4} {m['win_rate']:>5.0f}%{note}"
              f" {_fmt_r(m['expectancy_r']):>7} {_fmt_pnl(m['total_pnl']):>10}")
    print("  * fewer than 5 trades")


def print_report(db_path: str = None):
    if db_path is None:
        from .config import settings
        db_path = settings.db_path

    print("=" * 50)
    print("  microbot — Performance Report")
    print("=" * 50)

    swing = _load_swing_trades(db_path)
    intraday = _load_intraday_trades(db_path)

    _print_section(compute_metrics(swing), "Swing Trades")
    if swing:
        print("\n  By strategy:")
        _print_strategy_breakdown(swing)

    _print_section(compute_metrics(intraday), "Intraday / ORB")

    all_trades = [
        {"pnl": t["pnl"], "r_multiple": t["r_multiple"], "symbol": t["symbol"]}
        for t in swing + intraday
    ]
    _print_section(compute_metrics(all_trades), "Combined")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Print closed-trade performance summary.")
    parser.add_argument("--db", default=None, help="Path to microbot.db")
    args = parser.parse_args()
    print_report(db_path=args.db)
