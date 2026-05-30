#!/usr/bin/env python3
"""Convenience: scan + rank the universe, no trading. `python run_research.py`"""
from microbot.engine import run_once
run_once(research_only=True, push_sheets=True)
