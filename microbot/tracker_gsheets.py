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

# Per-column header colors (light pastel backgrounds with dark text)
_COL_COLORS_WL = [
    {"red": 0.68, "green": 0.85, "blue": 0.90},  # Symbol      — light blue
    {"red": 0.75, "green": 0.88, "blue": 0.75},  # Strategy    — light green
    {"red": 0.98, "green": 0.88, "blue": 0.68},  # Trades      — light orange
    {"red": 0.85, "green": 0.75, "blue": 0.92},  # Win Rate    — light purple
    {"red": 0.68, "green": 0.90, "blue": 0.85},  # Expectancy  — light teal
    {"red": 0.98, "green": 0.96, "blue": 0.68},  # Profit Fac  — light yellow
    {"red": 0.95, "green": 0.75, "blue": 0.75},  # Max DD      — light red
    {"red": 0.75, "green": 0.85, "blue": 0.98},  # Score       — light sky
]

_COL_COLORS_SG = [
    {"red": 0.68, "green": 0.85, "blue": 0.90},  # Symbol      — light blue
    {"red": 0.75, "green": 0.88, "blue": 0.75},  # Strategy    — light green
    {"red": 0.98, "green": 0.96, "blue": 0.68},  # Entry       — light yellow
    {"red": 0.95, "green": 0.75, "blue": 0.75},  # Stop        — light red
    {"red": 0.68, "green": 0.90, "blue": 0.80},  # Target      — light mint
    {"red": 0.75, "green": 0.85, "blue": 0.98},  # Score       — light sky
    {"red": 0.90, "green": 0.88, "blue": 0.98},  # Reason      — light lavender
]

_WHITE     = {"red": 1.0, "green": 1.0,  "blue": 1.0}
_DARK_TEXT = {"red": 0.15, "green": 0.15, "blue": 0.15}
_ROW_ALT   = {"red": 0.96, "green": 0.96, "blue": 0.98}  # subtle alternating row

_BORDER = {
    "style": "SOLID",
    "width": 1,
    "color": {"red": 0.7, "green": 0.7, "blue": 0.7}
}


def _client():
    import gspread
    return gspread.service_account(filename=settings.gsheet_creds)


def _col_letter(n: int) -> str:
    """Convert 1-based column index to letter (1→A, 2→B …)."""
    return chr(ord("A") + n - 1)


def _col_widths(ws, widths: List[int]):
    try:
        requests = [
            {"updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "COLUMNS",
                          "startIndex": i, "endIndex": i + 1},
                "properties": {"pixelSize": w},
                "fields": "pixelSize"
            }}
            for i, w in enumerate(widths)
        ]
        ws.spreadsheet.batch_update({"requests": requests})
    except Exception:
        pass


def _row_height(ws, start_row: int, end_row: int, height: int = 28):
    try:
        ws.spreadsheet.batch_update({"requests": [
            {"updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "ROWS",
                          "startIndex": start_row - 1, "endIndex": end_row},
                "properties": {"pixelSize": height},
                "fields": "pixelSize"
            }}
        ]})
    except Exception:
        pass


def _format_header_cells(ws, col_colors: List[dict], col_count: int):
    """Format each header cell with its own color, bold dark text, size 12, border."""
    for i, bg in enumerate(col_colors[:col_count]):
        col = _col_letter(i + 1)
        ws.format(f"{col}1", {
            "backgroundColor": bg,
            "textFormat": {
                "bold": True,
                "fontSize": 12,
                "foregroundColor": _DARK_TEXT,
            },
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
            "borders": {
                "top":    _BORDER, "bottom": _BORDER,
                "left":   _BORDER, "right":  _BORDER,
            },
        })


def _format_data_rows(ws, row_count: int, col_count: int):
    """Format data rows: font size 12, alternating bg, borders."""
    end_col = _col_letter(col_count)
    for i in range(2, row_count + 2):
        bg = _ROW_ALT if i % 2 == 0 else _WHITE
        ws.format(f"A{i}:{end_col}{i}", {
            "backgroundColor": bg,
            "textFormat": {"fontSize": 12, "foregroundColor": _DARK_TEXT},
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
            "borders": {
                "top":    _BORDER, "bottom": _BORDER,
                "left":   _BORDER, "right":  _BORDER,
            },
        })


def _ensure_ws(sh, title: str, headers: List[str]):
    try:
        ws = sh.worksheet(title)
    except Exception:
        ws = sh.add_worksheet(title=title, rows=200, cols=max(10, len(headers)))
    ws.clear()
    ws.append_row(headers)
    return ws


def _format_watchlist(ws, row_count: int):
    ws.freeze(rows=1)
    _format_header_cells(ws, _COL_COLORS_WL, 8)
    _format_data_rows(ws, row_count, 8)
    _col_widths(ws, [100, 160, 80, 100, 120, 120, 110, 90])
    _row_height(ws, 1, row_count + 1, 32)


def _format_signals(ws, row_count: int):
    ws.freeze(rows=1)
    _format_header_cells(ws, _COL_COLORS_SG, 7)
    _format_data_rows(ws, row_count, 7)
    _col_widths(ws, [100, 160, 90, 90, 90, 90, 320])
    _row_height(ws, 1, max(row_count + 1, 2), 32)


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
    rk_headers = ["Symbol", "Strategy", "Trades", "Win Rate",
                  "Expectancy R", "Profit Factor", "Max DD (R)", "Score"]
    ws = _ensure_ws(sh, "Watchlist", rk_headers)
    rk_keys = ["symbol", "strategy", "trades", "win_rate",
               "expectancy_r", "profit_factor", "max_dd_R", "score"]
    rows = [[r.get(k) for k in rk_keys] for r in result["rankings"][:50]]
    if rows:
        ws.append_rows(rows)
    _format_watchlist(ws, len(rows))

    # Live signals tab
    sg_headers = ["Symbol", "Strategy", "Entry", "Stop", "Target", "Score", "Reason"]
    ws2 = _ensure_ws(sh, "LiveSignals", sg_headers)
    sg_keys = ["symbol", "strategy", "entry", "stop", "target", "score", "reason"]
    rows2 = [[s.get(k) for k in sg_keys] for s in result["live_signals"]]
    if rows2:
        ws2.append_rows(rows2)
    _format_signals(ws2, len(rows2))

    print(f"  (gsheets) pushed {len(rows)} rankings, {len(rows2)} live signals.")
    return True
