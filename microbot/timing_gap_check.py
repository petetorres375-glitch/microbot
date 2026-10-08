"""
timing_gap_check.py
-------------------
Part A of "why do live trades lose when backtests say they win?" (2026-10-08).
READ-ONLY: downloads market data and reads the journal's active params. It
never places orders, never writes to the DB, never changes settings.

The question: the backtest and the live engine look at the market at
different moments.

  * Backtest ("completed view"): evaluates a strategy on a FINISHED daily bar
    and assumes the buy happens at that day's close (backtest.py).
  * Live engine ("9:35 view"): runs ~9:35 AM ET. Alpaca's daily bar for today
    only holds the first ~5 minutes of trading, and the strategy treats that
    tiny partial bar as "today" - its close, high, low and volume feed every
    indicator. The buy happens at roughly the 9:35 price.

For every trading day D in the test window, every symbol in the live
universe and every live strategy, this rebuilds BOTH views, records which
signals fire, plays each signal out with the backtest's own stop/target
rules, and compares the groups:

  both            - fired in the 9:35 view AND the completed view of day D
  live_only       - fired at 9:35 but NOT on day D's finished bar
                    (the market had changed its mind by the close)
  completed_only  - fired on the finished bar but not at 9:35
                    (backtest counted it, live never saw it)

If live_only signals lose money while completed signals make money, the
9:35 timing alone explains part of the backtest-to-live gap.

Simplifications (stated so the numbers are read correctly):
  * Signals are judged one at a time. Live-engine filters that depend on the
    account (already holding the symbol, sector cap, max positions, analyzer
    veto, morning verdict, backtest score > 0) are NOT applied - this measures
    the signal itself, not the portfolio.
  * The 9:35 view uses the 9:30-9:35 five-minute bar. The real run fetches
    each symbol somewhere between ~9:35 and ~9:39, so its partial bar holds a
    few more minutes than this one.

Run:  python -m microbot.timing_gap_check [--days 120]
"""
from __future__ import annotations

import argparse
import datetime as dt
from contextlib import contextmanager
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from . import indicators as ind
from .rebaseline import strategy_r_stats

NY = ZoneInfo("America/New_York")
OPEN_BAR_TIME = dt.time(9, 30)   # the 9:30-9:35 five-minute bar
SIM_RUN_TIME = dt.time(9, 35)    # when the live engine "looks"


# ---------------------------------------------------------------------------
# Pure helpers (no network) - these are what the unit tests cover.
# ---------------------------------------------------------------------------

def ny_dates(index: pd.DatetimeIndex) -> List[dt.date]:
    """Trading date of each bar, in New York time. Alpaca stamps daily bars
    at midnight ET expressed in UTC, so converting to NY gives the right day."""
    if index.tz is None:
        return [ts.date() for ts in index]
    return [ts.date() for ts in index.tz_convert(NY)]


def partial_bar(intraday_day: pd.DataFrame) -> Optional[Dict[str, float]]:
    """The 9:30 five-minute bar of one trading day, as an OHLCV dict.
    `intraday_day` must already be indexed in New York time.
    Returns None if that bar is missing (halt, no trades, data gap)."""
    first = intraday_day[intraday_day.index.time == OPEN_BAR_TIME]
    if first.empty:
        return None
    row = first.iloc[0]
    return {k: float(row[k]) for k in ("open", "high", "low", "close", "volume")}


def live_view(daily: pd.DataFrame, pos: int, bar: Dict[str, float]) -> pd.DataFrame:
    """What the live engine saw at 9:35 on the day at row `pos`: every
    completed bar BEFORE that day, plus that day's row replaced by the
    first-five-minutes partial bar (same timestamp as the real daily row)."""
    hist = daily.iloc[:pos]
    today = pd.DataFrame([bar], index=daily.index[pos:pos + 1])[list(daily.columns)]
    return pd.concat([hist, today])


def resolve(entry: float, stop: float, target: float,
            bars: Iterable[Tuple[float, float, float, float]]) -> Tuple[float, str]:
    """Play one long trade forward with the SAME rules as backtest.py:
    a bar that opens at/below the stop fills at its open (gap-through);
    a bar touching both stop and target counts as a stop (conservative);
    still open when bars run out -> closed at the last close ("open_end").
    `bars` yields (open, high, low, close). Returns (exit_price, outcome)."""
    last_close = entry
    for o, h, l, c in bars:
        last_close = c
        if o <= stop:
            return o, "stop"
        if l <= stop:
            return stop, "stop"
        if h >= target:
            return target, "target"
    return last_close, "open_end"


def r_multiple(entry: float, stop: float, exit_price: float) -> Optional[float]:
    risk = entry - stop
    if risk <= 0:
        return None
    return round((exit_price - entry) / risk, 3)


def classify(live: Dict[tuple, dict], completed: Dict[tuple, dict]) -> Dict[str, List[dict]]:
    """Split signals keyed by (symbol, strategy, date) into the three groups.
    Each group holds the signal records from the side(s) that fired:
      both           -> list of {"live": rec, "completed": rec}
      live_only      -> live records
      completed_only -> completed records"""
    keys_l, keys_c = set(live), set(completed)
    return {
        "both": [{"live": live[k], "completed": completed[k]} for k in sorted(keys_l & keys_c)],
        "live_only": [live[k] for k in sorted(keys_l - keys_c)],
        "completed_only": [completed[k] for k in sorted(keys_c - keys_l)],
    }


@contextmanager
def simulated_clock(when: dt.datetime):
    """Make indicators.pace_adjusted_volume() believe it is `when`, so the
    breakout volume check projects the partial bar exactly as it did live.
    Strategies call it as `ind.pace_adjusted_volume(df)`, so swapping the
    module attribute is enough; the original is always restored."""
    original = ind.pace_adjusted_volume
    ind.pace_adjusted_volume = lambda df, now=None: original(df, now=when)
    try:
        yield
    finally:
        ind.pace_adjusted_volume = original


def _rows(df: pd.DataFrame):
    return zip(df["open"], df["high"], df["low"], df["close"])


# ---------------------------------------------------------------------------
# Per-symbol replay
# ---------------------------------------------------------------------------

def replay_symbol(symbol: str, daily: pd.DataFrame, intraday: pd.DataFrame,
                  strategies, test_dates: List[dt.date]):
    """Replay both views for one symbol. Returns (live, completed) dicts keyed
    by (symbol, strategy, date) -> record with entry/stop/target/exit/r."""
    live: Dict[tuple, dict] = {}
    completed: Dict[tuple, dict] = {}
    d_dates = ny_dates(daily.index)
    pos_of = {d: i for i, d in enumerate(d_dates)}

    intra = intraday.copy()
    intra.index = intra.index.tz_convert(NY)
    intra_by_day = {d: g for d, g in intra.groupby(intra.index.date)}

    caches = {s.name: s.precompute(daily) for s in strategies}

    for day in test_dates:
        pos = pos_of.get(day)
        if pos is None or pos < 1:
            continue
        rest_daily = daily.iloc[pos + 1:]

        # Completed view: exactly what backtest.py does on day D.
        window = daily.iloc[:pos + 1]
        for s in strategies:
            sig = s.evaluate(symbol, window, cache=caches[s.name])
            if sig is None or sig.entry - sig.stop <= 0:
                continue
            exit_px, outcome = resolve(sig.entry, sig.stop, sig.target, _rows(rest_daily))
            completed[(symbol, s.name, day)] = {
                "symbol": symbol, "strategy": s.name, "date": day,
                "entry": sig.entry, "stop": sig.stop, "target": sig.target,
                "exit": exit_px, "outcome": outcome,
                "r_multiple": r_multiple(sig.entry, sig.stop, exit_px),
            }

        # 9:35 view: history + first-five-minutes bar, no cache (live
        # evaluates without one), clock frozen at 9:35 that day.
        day_intra = intra_by_day.get(day)
        if day_intra is None:
            continue
        bar = partial_bar(day_intra)
        if bar is None:
            continue
        view = live_view(daily, pos, bar)
        after_open = day_intra[day_intra.index.time > OPEN_BAR_TIME]
        when = dt.datetime.combine(day, SIM_RUN_TIME, tzinfo=NY)
        with simulated_clock(when):
            for s in strategies:
                sig = s.evaluate(symbol, view)
                if sig is None or sig.entry - sig.stop <= 0:
                    continue
                # Rest of day D in 5-min steps, then the following days.
                path = list(_rows(after_open)) + list(_rows(rest_daily))
                exit_px, outcome = resolve(sig.entry, sig.stop, sig.target, path)
                live[(symbol, s.name, day)] = {
                    "symbol": symbol, "strategy": s.name, "date": day,
                    "entry": sig.entry, "stop": sig.stop, "target": sig.target,
                    "exit": exit_px, "outcome": outcome,
                    "r_multiple": r_multiple(sig.entry, sig.stop, exit_px),
                }
    return live, completed


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _stats(records: List[dict]) -> Dict:
    return strategy_r_stats([r for r in records if r["r_multiple"] is not None])


def summarize(groups: Dict[str, List[dict]]) -> Dict[str, Dict]:
    """R stats per group. For 'both', report the live-side and completed-side
    outcomes separately (same signal, different entry price/time)."""
    both = groups["both"]
    return {
        "both_as_live": _stats([b["live"] for b in both]),
        "both_as_completed": _stats([b["completed"] for b in both]),
        "live_only": _stats(groups["live_only"]),
        "completed_only": _stats(groups["completed_only"]),
        "all_live": _stats([b["live"] for b in both] + groups["live_only"]),
        "all_completed": _stats([b["completed"] for b in both] + groups["completed_only"]),
    }


def by_strategy(groups: Dict[str, List[dict]]) -> Dict[str, Dict[str, Dict]]:
    names = sorted({r["strategy"] for g in ("live_only", "completed_only") for r in groups[g]}
                   | {b["live"]["strategy"] for b in groups["both"]})
    out = {}
    for n in names:
        sub = {
            "both": [b for b in groups["both"] if b["live"]["strategy"] == n],
            "live_only": [r for r in groups["live_only"] if r["strategy"] == n],
            "completed_only": [r for r in groups["completed_only"] if r["strategy"] == n],
        }
        out[n] = summarize(sub)
    return out


def print_report(groups, overall, per_strat, start, end, n_symbols) -> None:
    print(f"\nTiming gap check  {start} -> {end}  ({n_symbols} symbols)\n")
    rows = [
        ("All signals, 9:35 view (what live trades)", "all_live"),
        ("All signals, completed view (what backtest counts)", "all_completed"),
        ("  Fired in both - traded at 9:35", "both_as_live"),
        ("  Fired in both - traded at the close", "both_as_completed"),
        ("  Fired ONLY at 9:35", "live_only"),
        ("  Fired ONLY on the completed bar", "completed_only"),
    ]
    print(f"{'Group':<52} {'N':>5} {'Win%':>6} {'ExpR':>7} {'AvgWin':>7} {'AvgLoss':>8}")
    print("-" * 90)
    for label, key in rows:
        s = overall[key]
        print(f"{label:<52} {s['trades']:>5} {s['win_rate'] * 100:>5.1f}% "
              f"{s['expectancy_r']:>+7.3f} {s['avg_win_r']:>+7.3f} {s['avg_loss_r']:>+8.3f}")
    print("\nBy strategy (N / expectancy R):")
    print(f"{'Strategy':<20} {'live 9:35':>16} {'completed':>16} {'only 9:35':>16} {'only completed':>16}")
    for name, s in per_strat.items():
        cell = lambda k: f"{s[k]['trades']:>4} / {s[k]['expectancy_r']:>+6.3f}"
        print(f"{name:<20} {cell('all_live'):>16} {cell('all_completed'):>16} "
              f"{cell('live_only'):>16} {cell('completed_only'):>16}")


# ---------------------------------------------------------------------------
# Entry point (network + DB reads only)
# ---------------------------------------------------------------------------

def run(days: int = 120):
    from .config import settings
    from .data import MarketData
    from . import journal
    from .rebaseline import _combined_universe
    from .strategies import build_strategies_from_params

    rr = settings.reward_risk_ratio
    active = journal.fetch_active_params()
    default_strats = build_strategies_from_params(active, rr=rr)
    div_strats = build_strategies_from_params(active, rr=rr, dividend=True)
    symbols, dividend_set, ipo_set = _combined_universe()
    md = MarketData()

    # Calendar days of 5-min data needed to cover `days` trading days.
    intraday_lookback = int(days * 7 / 5) + 10

    live: Dict[tuple, dict] = {}
    completed: Dict[tuple, dict] = {}
    used = 0
    start = end = None
    for i, symbol in enumerate(symbols, 1):
        strats = div_strats if symbol in dividend_set else default_strats
        lb = settings.ipo_lookback_days if symbol in ipo_set else None
        try:
            daily = md.bars(symbol, timeframe="1Day", lookback_days=lb)
            intraday = md.bars(symbol, timeframe="5Min", lookback_days=intraday_lookback)
        except Exception as e:
            print(f"  ! {symbol}: data error {e}")
            continue
        if daily is None or daily.empty or len(daily) < 60 or intraday is None or intraday.empty:
            print(f"  - {symbol}: not enough data, skipped")
            continue
        # Only fully finished days: drop today if it's in the data.
        dates = [d for d in ny_dates(daily.index) if d < dt.date.today()]
        test_dates = dates[-days:]
        start = min(start, test_dates[0]) if start else test_dates[0]
        end = max(end, test_dates[-1]) if end else test_dates[-1]
        l, c = replay_symbol(symbol, daily, intraday, strats, test_dates)
        live.update(l)
        completed.update(c)
        used += 1
        print(f"  [{i}/{len(symbols)}] {symbol}: {len(l)} signals at 9:35, {len(c)} on completed bars")

    groups = classify(live, completed)
    overall = summarize(groups)
    per_strat = by_strategy(groups)
    print_report(groups, overall, per_strat, start, end, used)
    return groups, overall, per_strat


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--days", type=int, default=120, help="trading days to replay (default 120)")
    args = ap.parse_args()
    print(f"Replaying the last {args.days} trading days, 9:35 view vs completed view "
          f"(read-only; downloads daily + 5-minute bars for every symbol)...")
    run(days=args.days)
