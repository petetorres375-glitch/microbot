#!/usr/bin/env python3
"""
Run the walk-forward parameter optimizer.

Searches for better strategy parameters, validates on out-of-sample data,
and queues any improvements as proposals for your review. Nothing is
promoted automatically.

Usage:
    python run_optimizer.py               # full universe
    python run_optimizer.py --list        # just show pending proposals, no run

Review proposals after:
    python -m microbot.approvals --params
"""
import argparse
import json

from microbot import journal
from microbot.optimizer import run_optimization

parser = argparse.ArgumentParser()
parser.add_argument("--list", action="store_true",
                    help="list pending proposals and exit without running")
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
    run_optimization()
