#!/usr/bin/env python3
"""Convenience: scan + rank the universe, no trading. `python run_research.py`"""
import time
from microbot.engine import run_once
from microbot.tracker_gsheets import push_positions, push_daily_trades, _spy_benchmark

run_once(research_only=True, push_sheets=True)
print(f"\n--- Benchmark --- {_spy_benchmark()}")
time.sleep(5)  # small buffer; each tab push is now just 2 batched API calls (see tracker_gsheets._push_tab), so quota is no longer the bottleneck
push_positions()
push_daily_trades()

print("\nNew-stock discovery runs weekly: python run_symbol_discovery.py")
