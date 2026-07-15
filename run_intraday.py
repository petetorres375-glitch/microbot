"""
run_intraday.py
---------------
Day trading entry point. Runs the pre-market scanner then the ORB engine.

Usage:
    python run_intraday.py            # scan + trade (reuses today's cached scan if fresh)
    python run_intraday.py --rescan     # force a fresh scan instead of the cache
    python run_intraday.py --scan-only  # just print candidates, no trading
"""
import argparse
import datetime
import json
import os
import sys

from microbot.intraday_scanner import scan, CANDIDATES_FILE
from microbot.intraday_engine import IntradayEngine

LOCK_FILE = "intraday.lock"


def _load_cached_candidates(top: int) -> list | None:
    """Reuse the 9:15 AM cron's scan if it's from today, to avoid redoing the
    slow yfinance float/rel-vol lookups in the 9:34 AM hot path — that re-scan
    was delaying ORB entries by several minutes on slow-Yahoo mornings (2026-07-15:
    AEHR entry landed 6 min after the opening range closed, chasing an already
    extended move). The opening range itself is always computed from live Alpaca
    bars regardless of candidate source, so a slightly stale watchlist doesn't
    affect entry/stop accuracy.
    """
    try:
        with open(CANDIDATES_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("date") != datetime.date.today().isoformat():
        return None
    candidates = data.get("candidates") or []
    return candidates[:top] if candidates else None


def _acquire_lock() -> bool:
    if os.path.exists(LOCK_FILE):
        try:
            pid = int(open(LOCK_FILE).read().strip())
            # Check if that process is still alive
            os.kill(pid, 0)
            print(f"[intraday] already running (pid {pid}) — exiting to avoid duplicate trades.")
            return False
        except (ProcessLookupError, PermissionError):
            pass  # stale lock — process is gone
        except (ValueError, OSError):
            pass  # unreadable lock file
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def _release_lock():
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass


def main():
    p = argparse.ArgumentParser(description="Intraday day trading runner")
    p.add_argument("--scan-only", action="store_true",
                   help="Run scanner and print candidates without trading")
    p.add_argument("--top", type=int, default=5,
                   help="Max candidates to surface (default: 5)")
    p.add_argument("--rescan", action="store_true",
                   help="Force a fresh scan instead of reusing today's cached candidates")
    args = p.parse_args()

    candidates = None if args.rescan else _load_cached_candidates(args.top)
    if candidates is not None:
        print(f"=== Using cached candidates from today's earlier scan ({CANDIDATES_FILE}) ===")
    else:
        print("=== Pre-market gap scan ===")
        candidates = scan(top=args.top)

    if not candidates:
        print("No qualifying candidates today. Nothing to trade.")
        return

    print(f"\n{'Symbol':<8} {'Gap%':>6} {'Price':>7} {'RelVol':>7} {'Verdict'}")
    print("-" * 42)
    for c in candidates:
        print(f"{c['symbol']:<8} {c['gap_pct']:>5.1%} {c['price']:>7.2f} "
              f"{c['rel_volume']:>7.1f}x  {c['verdict']}")

    if args.scan_only:
        return

    if not _acquire_lock():
        sys.exit(1)

    try:
        print("\n=== Opening Range Breakout engine starting ===")
        engine = IntradayEngine()
        engine.run()
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
