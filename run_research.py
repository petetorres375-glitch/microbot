#!/usr/bin/env python3
"""Convenience: scan + rank the universe, no trading. `python run_research.py`"""
from microbot.engine import run_once
from microbot.yahoo_scanner import fetch_candidates
from microbot.tracker_gsheets import push_positions

run_once(research_only=True, push_sheets=True)
push_positions()

print("\n--- Yahoo Finance: new candidates outside universe ---")
candidates = fetch_candidates(top=10)
if not candidates:
    print("  (none)")
else:
    for r in candidates:
        sig = " >> LIVE SIGNAL" if r["live_signal"] else ""
        print(f"  {r['symbol']:<6} [{r['source']}]  ${r['price']:.2f}  "
              f"{r['strategy']}  trades={r['trades']}  "
              f"wr={r['win_rate']:.0%}  exp={r['expectancy_r']:.3f}R  "
              f"score={r['score']:.3f}{sig}")
        if r["live_signal"]:
            print(f"         entry={r['signal_entry']} stop={r['signal_stop']} "
                  f"target={r['signal_target']}")
