"""
verdicts.py
-----------
Reads and writes morning_verdicts.json — CLEAN/CAUTION/AVOID per symbol.

Written by the morning CCR analysis routine (see CLAUDE.md).
Read by the engine (to skip AVOID / flag CAUTION) and morning_review.py
(to surface CLEAN signals for interactive pick).

File format:
  {
    "date": "2026-06-02",
    "verdicts": {
      "BB": "CLEAN",
      "GLD": "CAUTION",
      "AMD": "AVOID"
    }
  }
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

VERDICTS_FILE = Path(__file__).parent.parent / "morning_verdicts.json"

VALID = {"CLEAN", "CAUTION", "AVOID"}


def load() -> dict:
    """Return {symbol: verdict} for today. Empty dict if file missing or stale."""
    if not VERDICTS_FILE.exists():
        return {}
    try:
        data = json.loads(VERDICTS_FILE.read_text())
        if data.get("date") != str(date.today()):
            return {}
        v = data.get("verdicts", {})
        return {k.upper(): val.upper() for k, val in v.items() if val.upper() in VALID}
    except Exception:
        return {}


def save(verdicts: dict) -> None:
    """Write today's verdicts. Called by the morning CCR routine."""
    clean = {k.upper(): v.upper() for k, v in verdicts.items() if v.upper() in VALID}
    VERDICTS_FILE.write_text(json.dumps(
        {"date": str(date.today()), "verdicts": clean}, indent=2))
    print(f"  verdicts saved: {len(clean)} symbols → {VERDICTS_FILE.name}")
