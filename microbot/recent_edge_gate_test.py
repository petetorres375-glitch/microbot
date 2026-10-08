"""
recent_edge_gate_test.py
------------------------
Step 3b of the 2026-10-08 investigation: would an automatic "recent results"
gate have helped? READ-ONLY - downloads bars and reads active params; never
trades, never writes, changes nothing live.

The proposed gate: before trading strategy S on date D, look at S's own
backtest trades (pooled across the universe) that had ALREADY CLOSED before
D and were entered within the previous `window_days` calendar days. If there
are at least `min_trades` of them and their average R is <= `threshold`,
S is gated OFF on D. Fewer than `min_trades` -> gate stays ON (no evidence,
no change from today's behavior).

Only trades closed before D are used, so the gate never sees the future -
it knows exactly what the live bot could have known that morning.

Every backtest trade in the full lookback is labelled gate-ON or gate-OFF at
its entry date. If the gate works, OFF trades should be clearly worse than
ON trades, and ON-only should beat taking everything. Trades entered before
a full window of history exists are reported separately ("warmup") and left
out of the comparison. Several window lengths are reported side by side on
purpose - picking whichever looks best would just overfit the gate itself.

Run:  python -m microbot.recent_edge_gate_test
"""
from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Tuple

from .rebaseline import strategy_r_stats

WINDOWS_DAYS = (91, 182, 365)   # ~3, 6, 12 months
MIN_TRADES = 10
THRESHOLD = 0.0


def gate_state(history: List[Dict], as_of: dt.date, window_days: int,
               min_trades: int = MIN_TRADES, threshold: float = THRESHOLD
               ) -> Tuple[bool, int, Optional[float]]:
    """Is the strategy allowed to trade on `as_of`? `history` = that
    strategy's trades (dicts with entry_date, exit_date, r_multiple).
    Uses only trades that EXITED strictly before `as_of` and ENTERED within
    the window. Returns (on, n_used, avg_r_or_None)."""
    start = as_of - dt.timedelta(days=window_days)
    used = [t["r_multiple"] for t in history
            if t["exit_date"] < as_of and t["entry_date"] >= start]
    if len(used) < min_trades:
        return True, len(used), None
    avg = sum(used) / len(used)
    return avg > threshold, len(used), round(avg, 3)


def label_trades(trades: List[Dict], window_days: int, first_date: dt.date,
                 min_trades: int = MIN_TRADES, threshold: float = THRESHOLD
                 ) -> Dict[str, List[Dict]]:
    """Label every trade by the gate state at its entry date, per strategy.
    Trades entered before `first_date + window_days` go to 'warmup'
    (the gate couldn't have had a full window yet)."""
    by_strat: Dict[str, List[Dict]] = {}
    for t in trades:
        by_strat.setdefault(t["strategy"], []).append(t)
    out = {"on": [], "off": [], "warmup": []}
    ready = first_date + dt.timedelta(days=window_days)
    for strat_trades in by_strat.values():
        for t in strat_trades:
            if t["entry_date"] < ready:
                out["warmup"].append(t)
                continue
            on, _, _ = gate_state(strat_trades, t["entry_date"], window_days,
                                  min_trades, threshold)
            out["on" if on else "off"].append(t)
    return out


def compare(labelled: Dict[str, List[Dict]]) -> Dict[str, Dict]:
    """R stats for: everything after warmup, gate-ON only, gate-OFF only."""
    return {
        "all": strategy_r_stats(labelled["on"] + labelled["off"]),
        "on": strategy_r_stats(labelled["on"]),
        "off": strategy_r_stats(labelled["off"]),
    }


def per_strategy(labelled: Dict[str, List[Dict]]) -> Dict[str, Dict[str, Dict]]:
    names = sorted({t["strategy"] for g in ("on", "off") for t in labelled[g]})
    return {n: compare({"on": [t for t in labelled["on"] if t["strategy"] == n],
                        "off": [t for t in labelled["off"] if t["strategy"] == n],
                        "warmup": []})
            for n in names}


def recent(labelled: Dict[str, List[Dict]], since: dt.date) -> Dict[str, Dict]:
    """Same comparison restricted to trades entered on/after `since`."""
    pick = lambda g: [t for t in labelled[g] if t["entry_date"] >= since]
    return compare({"on": pick("on"), "off": pick("off"), "warmup": []})


def _fmt(s: Dict) -> str:
    if not s["trades"]:
        return f"{'-':>22}"
    return f"{s['trades']:>5} / {s['win_rate'] * 100:>3.0f}% / {s['expectancy_r']:>+6.3f}"


def print_report(results: Dict[int, Dict], since: dt.date) -> None:
    print("\nCells: trades / win% / avg R.  'all' = no gate, 'on' = trades the gate "
          "would have allowed, 'off' = trades it would have blocked.\n")
    for w, r in results.items():
        print(f"=== window {w} days  (warmup trades excluded: {r['warmup']}) ===")
        print(f"{'':<22}{'all (no gate)':>24}{'gate ON (kept)':>24}{'gate OFF (blocked)':>24}")
        c = r["overall"]
        print(f"{'Whole history':<22}{_fmt(c['all']):>24}{_fmt(c['on']):>24}{_fmt(c['off']):>24}")
        c = r["recent"]
        print(f"{'Since ' + str(since):<22}{_fmt(c['all']):>24}{_fmt(c['on']):>24}{_fmt(c['off']):>24}")
        for name, c in r["per_strategy"].items():
            print(f"{'  ' + name:<22}{_fmt(c['all']):>24}{_fmt(c['on']):>24}{_fmt(c['off']):>24}")
        print()


def run(windows=WINDOWS_DAYS, min_trades: int = MIN_TRADES, threshold: float = THRESHOLD,
        recent_days: int = 182):
    from .backtest import backtest_symbol
    from .config import settings
    from .data import MarketData
    from . import journal
    from .rebaseline import _combined_universe
    from .strategies import build_strategies_from_params
    from .timing_gap_check import ny_dates

    rr = settings.reward_risk_ratio
    active = journal.fetch_active_params()
    # Gate is tested on EVERY strategy the engine knows (incl. paused ones),
    # since a paused strategy is exactly what the gate might re-enable.
    saved = settings.disabled_strategies
    try:
        settings.disabled_strategies = set()
        default_strats = build_strategies_from_params(active, rr=rr)
        div_strats = build_strategies_from_params(active, rr=rr, dividend=True)
    finally:
        settings.disabled_strategies = saved
    symbols, dividend_set, ipo_set = _combined_universe()
    md = MarketData()

    trades: List[Dict] = []
    first_date = None
    for i, symbol in enumerate(symbols, 1):
        strats = div_strats if symbol in dividend_set else default_strats
        lb = settings.ipo_lookback_days if symbol in ipo_set else None
        try:
            df = md.bars(symbol, lookback_days=lb)
        except Exception as e:
            print(f"  ! {symbol}: data error {e}")
            continue
        if df is None or df.empty or len(df) < 60:
            continue
        dates = ny_dates(df.index)
        first_date = min(first_date, dates[0]) if first_date else dates[0]
        n = 0
        for s in strats:
            for t in backtest_symbol(s, symbol, df):
                if t["outcome"] == "open_end":
                    continue  # never closed - can't count as a finished result
                t["entry_date"] = dates[t["entry_idx"]]
                t["exit_date"] = dates[t["exit_idx"]]
                trades.append(t)
                n += 1
        print(f"  [{i}/{len(symbols)}] {symbol}: {n} closed trades")

    since = dt.date.today() - dt.timedelta(days=recent_days)
    results = {}
    for w in windows:
        lab = label_trades(trades, w, first_date, min_trades, threshold)
        results[w] = {"warmup": len(lab["warmup"]), "overall": compare(lab),
                      "recent": recent(lab, since), "per_strategy": per_strategy(lab)}
    print_report(results, since)
    return results


if __name__ == "__main__":
    print("Testing the recent-results gate on history (read-only; walk-forward, "
          "no lookahead)...")
    run()
