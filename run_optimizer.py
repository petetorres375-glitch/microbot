#!/usr/bin/env python3
"""
Run the walk-forward parameter optimizer.

Searches for better strategy parameters, validates on out-of-sample data,
and queues any improvements as proposals for your review. Nothing is
promoted automatically.

Usage:
    python run_optimizer.py               # full universe, all strategies
    python run_optimizer.py --strategy ema_pullback [--strategy breakout_52w]  # just these
    python run_optimizer.py --list        # just show pending proposals, no run

Review proposals after:
    python import_proposals.py            # import remote proposals into local DB
    python -m microbot.approvals --params # approve / reject interactively
"""
import argparse
import json
import os
from datetime import datetime, timezone

from microbot import journal
from microbot.optimizer import run_optimization

PROPOSALS_FILE = os.path.join(os.path.dirname(__file__), "optimizer_proposals.json")

parser = argparse.ArgumentParser()
parser.add_argument("--list", action="store_true",
                    help="list pending proposals and exit without running")
parser.add_argument("--strategy", action="append", dest="strategies",
                    help="limit the run to this strategy (repeatable). Default: all strategies.")
args = parser.parse_args()

journal.init()

if args.list:
    proposals = journal.fetch_pending_proposals()
    if not proposals:
        print("No pending proposals.")
    for p in proposals:
        proposed = json.loads(p["proposed_params_json"])
        print(f"#{p['id']}  {p['strategy']}  +{p['improvement_pct']:.1f}% OOS  "
              f"params: {json.dumps(proposed)}")
else:
    proposals = run_optimization(strategies=args.strategies)

    # Write proposals to a JSON file so remote runs can push them back to the
    # repo and the user can import them locally via import_proposals.py.
    if proposals:
        payload = {
            "run_ts": datetime.now(timezone.utc).isoformat(),
            "proposals": [
                {
                    "strategy":          p["strategy"],
                    "proposed_params":   p["proposed_params"],
                    "current_params":    p["current_params"],
                    "is_score":          p["is_score"],
                    "oos_score":         p["oos_score"],
                    "current_oos_score": p["current_oos_score"],
                    "improvement_pct":   p["improvement_pct"],
                }
                for p in proposals
            ],
        }
        with open(PROPOSALS_FILE, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"\nProposals written to {PROPOSALS_FILE}")
        print("Push this file, then on your local machine run:")
        print("  python import_proposals.py")
        print("  python -m microbot.approvals --params")
