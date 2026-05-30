#!/usr/bin/env python3
"""Analyze the bot's closed trades. `python run_analyzer.py`
Prints the report and saves equity_curve.png, pnl_by_strategy.png,
and microbot_trades.xlsx."""
from microbot import analyzer
analyzer.print_report()
