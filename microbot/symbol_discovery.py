"""
symbol_discovery.py
-------------------
Weekly: take Yahoo trending/most-active candidates (yahoo_scanner), drop
anything excluded or already known, then for each one:

  1. backtest the live strategy set on full LOOKBACK_DAYS history
  2. pick the strategy by IN-SAMPLE (first 75%) expectancy only
  3. report that pick's OUT-OF-SAMPLE (last 25%) result — picking by OOS
     would make the OOS check meaningless
  4. check that at least 1 share fits the per-trade risk budget of the
     planned LIVE account (DISCOVERY_SIZING_EQUITY), not the paper equity

Passing candidates are saved as 'pending' for human review
(python -m microbot.approvals --symbols); failures are saved as 'rejected'
with the reason. Losing backtests and human "no"s are never re-proposed;
thin-sample and sizing rejections are re-checked after 90 days.

Run:  python run_symbol_discovery.py [--force]
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd

from . import journal, yahoo_scanner
from . import indicators as ind
from .backtest import backtest_symbol
from .config import settings
from .overfitting_check import IS_SPLIT, split_trades_by_period
from .rebaseline import strategy_r_stats
from .risk import RiskConfig, size_trade
from .strategies import Signal, _bracket, build_strategies_from_params

SCAN_KEY = "symbol_discovery"
MIN_IS_TRADES = 5
MIN_OOS_TRADES = 5
MIN_BARS = 60

# Rejections that mean "not enough evidence yet" / "doesn't fit the budget
# yet" — re-checked after RETRY_AFTER_DAYS. Every other rejection (a losing
# backtest, or a human "no") is permanent.
RETRYABLE_NOTE_PREFIXES = ("oos sample <", "no strategy with", "sizing:")
RETRY_AFTER_DAYS = 90


def _is_retryable(note: str | None) -> bool:
    return (note or "").startswith(RETRYABLE_NOTE_PREFIXES)


def _blocked_rejections(now: datetime | None = None) -> set:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=RETRY_AFTER_DAYS)
    blocked = set()
    for r in journal.fetch_rejected_discovered():
        recent = r["decided_ts"] and datetime.fromisoformat(r["decided_ts"]) > cutoff
        if not _is_retryable(r["note"]) or recent:
            blocked.add(r["symbol"])
    return blocked


def _excluded_symbols() -> set:
    excluded = {s.upper() for s in settings.universe_exclusions}
    excluded |= yahoo_scanner._current_universe()
    excluded |= set(journal.fetch_approved_universe())
    excluded |= _blocked_rejections()
    excluded |= {d["symbol"] for d in journal.fetch_pending_discovered()}
    return excluded


def _live_strategies() -> list:
    return build_strategies_from_params(journal.fetch_active_params(),
                                        rr=settings.reward_risk_ratio)


def _sizing_signal(strat, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
    atr = float(ind.atr(df, strat.atr_period).iloc[-1])
    if not atr or atr != atr:  # zero or NaN
        return None
    close = float(df["close"].iloc[-1])
    sig = _bracket(symbol, strat.name, close, atr, strat.stop_mult, strat.rr,
                   "discovery sizing check")
    return sig


def _evaluate_candidate(c: Dict, df: pd.DataFrame, strategies: list) -> tuple[Dict, str, str]:
    """Returns (row, status, note). status is 'pending' or 'rejected'."""
    symbol = c["symbol"]
    row = {"symbol": symbol, "source": c.get("source"), "sizing_ok": False,
           "price": round(float(df["close"].iloc[-1]), 2)}

    best = None
    for strat in strategies:
        if len(df) < strat.min_bars():
            continue
        trades = backtest_symbol(strat, symbol, df)
        is_t, oos_t = split_trades_by_period(trades, len(df), IS_SPLIT)
        is_s = strategy_r_stats(is_t)
        if is_s["trades"] < MIN_IS_TRADES:
            continue
        if best is None or is_s["expectancy_r"] > best[1]["expectancy_r"]:
            best = (strat, is_s, strategy_r_stats(oos_t))

    if best is None:
        return row, "rejected", f"no strategy with {MIN_IS_TRADES}+ in-sample trades"

    strat, is_s, oos_s = best
    row.update({"strategy": strat.name,
                "is_expectancy_r": is_s["expectancy_r"], "is_trades": is_s["trades"],
                "oos_expectancy_r": oos_s["expectancy_r"], "oos_trades": oos_s["trades"],
                "oos_win_rate": oos_s["win_rate"]})

    sig = _sizing_signal(strat, symbol, df)
    if sig is not None:
        row["risk_per_share"] = round(sig.risk_per_share, 4)
        cfg = RiskConfig(risk_per_trade_pct=settings.risk_per_trade_pct,
                         max_open_positions=settings.max_open_positions)
        eq = settings.discovery_sizing_equity
        row["sizing_ok"] = size_trade(sig, eq, eq, cfg) is not None

    if is_s["expectancy_r"] <= 0:
        return row, "rejected", "in-sample expectancy <= 0"
    if oos_s["trades"] < MIN_OOS_TRADES:
        return row, "rejected", f"oos sample < {MIN_OOS_TRADES} trades"
    if oos_s["expectancy_r"] <= 0:
        return row, "rejected", "oos expectancy <= 0"
    if not row["sizing_ok"]:
        return row, "rejected", "sizing: 1 share exceeds risk budget"
    return row, "pending", ""


def _throttled(min_interval_days: int) -> bool:
    last = journal.get_scan_log(SCAN_KEY)
    if not last:
        return False
    return datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(days=min_interval_days)


def run_discovery(top: int = 25, min_interval_days: int = 7, force: bool = False,
                  md=None) -> List[Dict]:
    """Returns the saved rows (each with id, status, note). Empty list if throttled."""
    journal.init()
    if not force and _throttled(min_interval_days):
        print(f"  symbol discovery ran within the last {min_interval_days} days — skipping "
              "(use --force to override)")
        return []

    excluded = _excluded_symbols()
    candidates = [c for c in yahoo_scanner.fetch_candidates(top=top)
                  if c["symbol"].upper() not in excluded]
    print(f"  {len(candidates)} new candidate(s) after exclusions")

    if md is None:
        from .data import MarketData
        md = MarketData()
    strategies = _live_strategies()

    saved = []
    for c in candidates:
        try:
            df = md.bars(c["symbol"])
        except Exception as e:
            print(f"  ! {c['symbol']}: data error {e}")
            continue
        if df is None or df.empty or len(df) < MIN_BARS:
            continue
        row, status, note = _evaluate_candidate(c, df, strategies)
        row["id"] = journal.save_discovered_symbol(row, status=status, note=note)
        row["status"], row["note"] = status, note
        saved.append(row)
        print(f"  {row['symbol']:<6} {status:<8} {note}")

    journal.set_scan_log(SCAN_KEY)
    return saved
