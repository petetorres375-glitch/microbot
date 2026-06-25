"""
run_intraday.py
---------------
Day trading entry point. Runs the pre-market scanner then the ORB engine.

Usage:
    python run_intraday.py            # scan + trade
    python run_intraday.py --scan-only  # just print candidates, no trading
"""
import argparse
import os
import sys

from microbot.intraday_scanner import scan
from microbot.intraday_engine import IntradayEngine

LOCK_FILE = "intraday.lock"


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
    args = p.parse_args()

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
