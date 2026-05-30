#!/usr/bin/env python3
"""
Import optimizer proposals from optimizer_proposals.json into the local DB.

The weekly remote optimizer run commits optimizer_proposals.json to the repo.
Pull it, then run this script to load the proposals into your local
microbot.db so you can review them with:

    python -m microbot.approvals --params
"""
import json
import os
import sys

from microbot import journal

PROPOSALS_FILE = os.path.join(os.path.dirname(__file__), "optimizer_proposals.json")

if not os.path.exists(PROPOSALS_FILE):
    print(f"No {PROPOSALS_FILE} found. Pull the latest changes first.")
    sys.exit(1)

with open(PROPOSALS_FILE) as f:
    data = json.load(f)

journal.init()

run_ts = data.get("run_ts", "unknown")
proposals = data.get("proposals", [])

if not proposals:
    print(f"No proposals in file (run_ts: {run_ts}).")
    sys.exit(0)

print(f"Importing {len(proposals)} proposal(s) from optimizer run at {run_ts}\n")
imported = 0
for p in proposals:
    pid = journal.save_param_proposal(p)
    print(f"  #{pid}  {p['strategy']}  +{p['improvement_pct']:.1f}% OOS  "
          f"proposed: {json.dumps(p['proposed_params'])}")
    imported += 1

print(f"\n{imported} proposal(s) imported.")
print("Review them with:  python -m microbot.approvals --params")
