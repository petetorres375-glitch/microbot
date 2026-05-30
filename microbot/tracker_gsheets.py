"""
tracker_gsheets.py
------------------
Optional: mirror the watchlist, research rankings, and live signals into a
Google Sheet so you can follow "stocks to be analyzed" from your phone.

Setup (one time):
  1. Google Cloud Console -> create a project -> enable Google Sheets API + Drive API.
  2. Create a Service Account -> create a JSON key -> save as service_account.json.
  3. Share your Google Sheet with the service account's email (Editor).
  4. Put the sheet id in .env as GSHEET_ID and the json path in
     GOOGLE_APPLICATION_CREDENTIALS.

This module degrades gracefully: if gspread isn't installed or creds are missing,
it no-ops with a printed warning instead of crashing the bot.
"""
from __future__ import annotations

from typing import Dict, List

from .config import settings


def _client():
    import gspread
    return gspread.service_account(filename=settings.gsheet_creds)


def _ensure_ws(sh, title: str, headers: List[str]):
    try:
        ws = sh.worksheet(title)
    except Exception:
        ws = sh.add_worksheet(title=title, rows=200, cols=max(10, len(headers)))
    ws.clear()
    ws.append_row(headers)
    return ws


def push_research(result: Dict) -> bool:
    if not settings.gsheet_id:
        print("  (gsheets) GSHEET_ID not set — skipping.")
        return False
    try:
        sh = _client().open_by_key(settings.gsheet_id)
    except Exception as e:
        print(f"  (gsheets) skipped: {e}")
        return False

    # Watchlist / rankings tab
    rk_headers = ["symbol", "strategy", "trades", "win_rate",
                  "expectancy_r", "profit_factor", "max_dd_R", "score"]
    ws = _ensure_ws(sh, "Watchlist", rk_headers)
    rows = [[r.get(h) for h in rk_headers] for r in result["rankings"][:50]]
    if rows:
        ws.append_rows(rows)

    # Live signals tab
    sg_headers = ["symbol", "strategy", "entry", "stop", "target", "score", "reason"]
    ws2 = _ensure_ws(sh, "LiveSignals", sg_headers)
    rows2 = [[s.get(h) for h in sg_headers] for s in result["live_signals"]]
    if rows2:
        ws2.append_rows(rows2)
    print(f"  (gsheets) pushed {len(rows)} rankings, {len(rows2)} live signals.")
    return True
