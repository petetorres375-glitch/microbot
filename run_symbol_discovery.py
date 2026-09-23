#!/usr/bin/env python3
"""
Weekly symbol discovery: find new stocks outside the universe, OOS-check and
size-check them, and queue the ones that pass for your review. Nothing is
added to the universe automatically.

Usage:
    python run_symbol_discovery.py            # weekly-throttled run
    python run_symbol_discovery.py --force    # run even if it ran < 7 days ago
    python run_symbol_discovery.py --list     # show pending candidates, no run

Review after:
    python -m microbot.approvals --symbols
"""
import argparse

from microbot import journal
from microbot.approvals import _format_symbol
from microbot.symbol_discovery import run_discovery

parser = argparse.ArgumentParser()
parser.add_argument("--top", type=int, default=25,
                    help="how many Yahoo candidates to evaluate (default 25)")
parser.add_argument("--force", action="store_true", help="bypass the weekly throttle")
parser.add_argument("--list", action="store_true", help="list pending candidates and exit")
args = parser.parse_args()

journal.init()

if not args.list:
    print("Running symbol discovery (Yahoo trending + most active)...")
    rows = run_discovery(top=args.top, force=args.force)
    pending = [r for r in rows if r["status"] == "pending"]
    print(f"\n{len(rows)} evaluated: {len(pending)} queued for review, "
          f"{len(rows) - len(pending)} auto-rejected.")

queue = journal.fetch_pending_discovered()
if queue:
    print(f"\n{len(queue)} candidate(s) awaiting review:")
    for d in queue:
        print("\n".join(_format_symbol(d)))
    print("\nReview with:  python -m microbot.approvals --symbols")
else:
    print("\nNo candidates awaiting review.")
