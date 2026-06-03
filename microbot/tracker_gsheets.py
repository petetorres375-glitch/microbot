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
    {"red": 0.88, "green": 0.98, "blue": 0.78},  # Dividend    — light lime
]

_COL_COLORS_SG = [
    {"red": 0.68, "green": 0.85, "blue": 0.90},  # Symbol      — light blue
    {"red": 0.75, "green": 0.88, "blue": 0.75},  # Strategy    — light green
    {"red": 0.98, "green": 0.96, "blue": 0.68},  # Entry       — light yellow
    {"red": 0.95, "green": 0.75, "blue": 0.75},  # Stop        — light red
    {"red": 0.68, "green": 0.90, "blue": 0.80},  # Target      — light mint
    {"red": 0.75, "green": 0.85, "blue": 0.98},  # Score       — light sky
    {"red": 0.88, "green": 0.98, "blue": 0.78},  # Dividend    — light lime
    {"red": 0.90, "green": 0.88, "blue": 0.98},  # Reason      — light lavender
]

_COL_COLORS_POS = [
    {"red": 0.68, "green": 0.85, "blue": 0.90},  # Symbol      — light blue
    {"red": 0.98, "green": 0.88, "blue": 0.68},  # Shares      — light orange
    {"red": 0.98, "green": 0.96, "blue": 0.68},  # Entry       — light yellow
    {"red": 0.75, "green": 0.92, "blue": 0.75},  # Current     — light green
    {"red": 0.68, "green": 0.90, "blue": 0.85},  # P&L $       — light teal
    {"red": 0.85, "green": 0.75, "blue": 0.92},  # P&L %       — light purple
    {"red": 0.95, "green": 0.75, "blue": 0.75},  # Stop        — light red
    {"red": 0.68, "green": 0.90, "blue": 0.80},  # Target      — light mint
    {"red": 0.98, "green": 0.96, "blue": 0.68},  # Health      — light yellow
]

_COL_COLORS_DT = [
    {"red": 0.68, "green": 0.85, "blue": 0.90},  # Symbol      — light blue
    {"red": 0.75, "green": 0.88, "blue": 0.75},  # Strategy    — light green
    {"red": 0.98, "green": 0.88, "blue": 0.68},  # Qty         — light orange
    {"red": 0.98, "green": 0.96, "blue": 0.68},  # Entry       — light yellow
    {"red": 0.95, "green": 0.75, "blue": 0.75},  # Stop        — light red
    {"red": 0.68, "green": 0.90, "blue": 0.80},  # Target      — light mint
    {"red": 0.85, "green": 0.75, "blue": 0.92},  # $ Risk      — light purple
    {"red": 0.90, "green": 0.88, "blue": 0.98},  # Time        — light lavender
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


_STRATEGY_GUIDE = [
    ("", ""),
    ("Strategies", ""),
    ("trend_momentum",    "EMA crossover + ADX filter — rides stocks in a confirmed uptrend"),
    ("mean_reversion",    "RSI + Bollinger Band dip within an uptrend — buys the pullback"),
    ("breakout",          "Donchian channel break + volume confirmation — catches momentum breakouts"),
    ("ema_pullback",      "Triple EMA alignment (21>50>150) + low-volume pullback — Stage 2 uptrend entries"),
    ("breakout_52w",      "Near 200-day high + 1.5× volume — institutional-grade breakout signal"),
    ("dividend_momentum", "Slow EMA (50/100), relaxed ADX, RSI < 65 — income-focused swing entries"),
]

_WL_GUIDE = [
    ("Symbol",        "Stock ticker symbol"),
    ("Strategy",      "Trading strategy that generated the signal (see Strategies section below)"),
    ("Trades",        "Number of historical backtested trades"),
    ("Win Rate",      "Percentage of trades that were profitable (0.6 = 60%)"),
    ("Expectancy R",  "Average profit per trade in units of risk (1.0R = gained 1× your risk on average)"),
    ("Profit Factor", "Gross profit ÷ gross loss — above 1.5 is healthy"),
    ("Max DD (R)",    "Worst historical drawdown in R-multiples (losing streak depth)"),
    ("Score",         "Drawdown-adjusted rank score: expectancy × √trades × DD penalty. Higher is better; 0 means insufficient history. Deep drawdown names are dampened even if expectancy looks good."),
    ("Dividend",      "YES = dividend-focused strategy (slow EMA, relaxed ADX, high yield focus)"),
] + _STRATEGY_GUIDE

_SG_GUIDE = [
    ("Symbol",   "Stock ticker symbol"),
    ("Strategy", "Strategy that triggered today's signal (see Strategies section below)"),
    ("Entry",    "Suggested entry price for the bracket order"),
    ("Stop",     "Stop-loss price — order exits automatically if price falls here"),
    ("Target",   "Take-profit price — order exits automatically if price reaches here"),
    ("Score",    "Drawdown-adjusted rank score — deep historical drawdowns are penalized even with high expectancy"),
    ("Dividend", "YES = dividend-focused strategy"),
    ("Reason",   "Technical conditions that triggered the signal (EMA, ADX, RSI values)"),
] + _STRATEGY_GUIDE

_POS_GUIDE = [
    ("Symbol",   "Stock ticker symbol"),
    ("Shares",   "Number of shares currently held"),
    ("Entry",    "Average price paid per share"),
    ("Current",  "Latest market price"),
    ("P&L $",    "Unrealized dollar gain/loss on the position"),
    ("P&L %",    "Unrealized percentage gain/loss from entry"),
    ("Stop",     "Stop-loss price from the bracket order — position exits automatically here"),
    ("Target",   "Take-profit price from the bracket order — position exits automatically here"),
    ("Health",   "Trade quality: R-multiple showing how far price has moved toward target vs stop. "
                 "+1.0R = at target, -1.0R = at stop. STRONG (>+1R) / WINNING / BREAKEVEN / AT RISK"),
]

_DT_GUIDE = [
    ("Symbol",   "Stock ticker symbol"),
    ("Strategy", "Strategy that triggered the signal"),
    ("Qty",      "Number of shares approved for the order"),
    ("Entry",    "Entry price for the bracket order"),
    ("Stop",     "Stop-loss price — exits automatically if price falls here"),
    ("Target",   "Take-profit price — exits automatically if price reaches here"),
    ("$ Risk",   "Dollar amount at risk on this trade (qty × |entry - stop|)"),
    ("Time",     "Time the trade was approved today (local time)"),
]

_GUIDE_HEADER_BG = {"red": 0.25, "green": 0.25, "blue": 0.25}
_GUIDE_HEADER_FG = {"red": 1.0,  "green": 1.0,  "blue": 1.0}
_GUIDE_LABEL_BG  = {"red": 0.93, "green": 0.93, "blue": 0.93}
_GUIDE_LABEL_FG  = {"red": 0.15, "green": 0.15, "blue": 0.15}


_STAMP_BG = {"red": 0.18, "green": 0.38, "blue": 0.62}  # navy blue
_STAMP_FG = {"red": 1.0,  "green": 1.0,  "blue": 1.0}


def _write_timestamp(ws, col_count: int):
    """Format the timestamp banner at row 1 (content already written by _ensure_ws)."""
    end_col = _col_letter(col_count)
    ws.merge_cells(f"A1:{end_col}1")
    ws.format(f"A1:{end_col}1", {
        "backgroundColor": _STAMP_BG,
        "textFormat": {"bold": True, "fontSize": 13, "foregroundColor": _STAMP_FG},
        "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
    })


def _add_guide(ws, data_rows: int, guide: list, col_count: int):
    """Write a column-guide legend below the data table (batched to stay under API quota)."""
    start = data_rows + 4  # +1 timestamp row, +1 header row, +1 gap, +1
    end_col = _col_letter(col_count)
    n = len(guide)

    # Write all values in one call
    values = [["Column Guide"] + [""] * (col_count - 1)]
    values += [[label, desc] + [""] * (col_count - 2) for label, desc in guide]
    ws.update(values=values, range_name=f"A{start}")

    # Merge header row
    ws.merge_cells(f"A{start}:{end_col}{start}")

    # Format header, labels, and descriptions — 3 calls total
    ws.format(f"A{start}:{end_col}{start}", {
        "backgroundColor": _GUIDE_HEADER_BG,
        "textFormat": {"bold": True, "fontSize": 14, "foregroundColor": _GUIDE_HEADER_FG},
        "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
    })
    ws.format(f"A{start + 1}:A{start + n}", {
        "backgroundColor": _GUIDE_LABEL_BG,
        "textFormat": {"bold": True, "fontSize": 14, "foregroundColor": _GUIDE_LABEL_FG},
        "horizontalAlignment": "LEFT", "verticalAlignment": "MIDDLE",
    })
    ws.format(f"B{start + 1}:{end_col}{start + n}", {
        "backgroundColor": _GUIDE_LABEL_BG,
        "textFormat": {"fontSize": 14, "foregroundColor": _GUIDE_LABEL_FG},
        "horizontalAlignment": "LEFT", "verticalAlignment": "MIDDLE",
    })


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
    """Format each header cell (row 2) with its own color, bold dark text, size 12, border."""
    for i, bg in enumerate(col_colors[:col_count]):
        col = _col_letter(i + 1)
        ws.format(f"{col}2", {
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
    """Format data rows: font size 12, alternating bg, borders — batched into 2 API calls."""
    if row_count == 0:
        return
    end_col = _col_letter(col_count)
    base_style = {
        "textFormat": {"fontSize": 12, "foregroundColor": _DARK_TEXT},
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
        "borders": {"top": _BORDER, "bottom": _BORDER, "left": _BORDER, "right": _BORDER},
    }
    even_rows = [f"A{i}:{end_col}{i}" for i in range(3, row_count + 3) if i % 2 == 0]
    odd_rows  = [f"A{i}:{end_col}{i}" for i in range(3, row_count + 3) if i % 2 != 0]
    if even_rows:
        ws.format(even_rows, {**base_style, "backgroundColor": _ROW_ALT})
    if odd_rows:
        ws.format(odd_rows,  {**base_style, "backgroundColor": _WHITE})


def _ensure_ws(sh, title: str, headers: List[str]):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).astimezone()
    stamp = now.strftime("%B %d, %Y  %I:%M %p")
    try:
        ws = sh.worksheet(title)
    except Exception:
        ws = sh.add_worksheet(title=title, rows=200, cols=max(10, len(headers)))
    ws.clear()
    # Unmerge all cells so stale guide merges don't block data rows on the next write
    try:
        sh.batch_update({"requests": [{"unmergeCells": {
            "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 200,
                      "startColumnIndex": 0, "endColumnIndex": 26}
        }}]})
    except Exception:
        pass
    ws.update(values=[[f"microbot  |  Last Updated: {stamp}"] + [""] * (len(headers) - 1)], range_name="A1")
    ws.update(values=[headers], range_name="A2")
    return ws


def _format_watchlist(ws, row_count: int):
    ws.freeze(rows=2)
    _write_timestamp(ws, 9)
    _format_header_cells(ws, _COL_COLORS_WL, 9)
    _format_data_rows(ws, row_count, 9)
    _col_widths(ws, [218, 160, 80, 100, 120, 120, 110, 90, 80])
    _row_height(ws, 1, row_count + 2, 32)
    _add_guide(ws, row_count, _WL_GUIDE, 9)


def _format_signals(ws, row_count: int):
    ws.freeze(rows=2)
    _write_timestamp(ws, 8)
    _format_header_cells(ws, _COL_COLORS_SG, 8)
    _format_data_rows(ws, row_count, 8)
    _col_widths(ws, [218, 160, 90, 90, 90, 90, 80, 320])
    _row_height(ws, 1, max(row_count + 2, 3), 32)
    _add_guide(ws, row_count, _SG_GUIDE, 8)


def _format_positions(ws, row_count: int):
    ws.freeze(rows=2)
    _write_timestamp(ws, 9)
    _format_header_cells(ws, _COL_COLORS_POS, 9)
    _format_data_rows(ws, row_count, 9)
    _col_widths(ws, [218, 80, 100, 100, 100, 100, 100, 100, 120])
    _row_height(ws, 1, max(row_count + 2, 3), 32)
    _add_guide(ws, row_count, _POS_GUIDE, 9)


def _format_daily_trades(ws, row_count: int):
    ws.freeze(rows=2)
    _write_timestamp(ws, 8)
    _format_header_cells(ws, _COL_COLORS_DT, 8)
    _format_data_rows(ws, row_count, 8)
    _col_widths(ws, [218, 160, 70, 100, 100, 100, 90, 160])
    _row_height(ws, 1, max(row_count + 2, 3), 32)
    _add_guide(ws, row_count, _DT_GUIDE, 8)


def _native(v):
    """Convert numpy scalars to plain Python types so gspread can serialize them."""
    try:
        return v.item()
    except AttributeError:
        return v


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
                  "Expectancy R", "Profit Factor", "Max DD (R)", "Score", "Dividend"]
    ws = _ensure_ws(sh, "Watchlist", rk_headers)
    rk_keys = ["symbol", "strategy", "trades", "win_rate",
               "expectancy_r", "profit_factor", "max_dd_R", "score", "dividend"]
    best: dict = {}
    for r in result["rankings"]:
        sym = r.get("symbol")
        score = r.get("score") or 0
        exp = r.get("expectancy_r") or 0
        trades = r.get("trades") or 0
        if score <= 0 and not (trades >= 3 and exp > 0):
            continue
        prev = best.get(sym)
        if prev is None:
            best[sym] = r
        else:
            prev_score = prev.get("score") or 0
            if score > prev_score or (score == prev_score and exp > (prev.get("expectancy_r") or 0)):
                best[sym] = r
    rows = [[("YES" if r.get(k) else ("" if k == "dividend" else _native(r.get(k)))) if k == "dividend"
             else _native(r.get(k)) for k in rk_keys]
            for r in sorted(best.values(), key=lambda r: r.get("symbol", ""))]
    if rows:
        ws.update(values=rows, range_name="A3")
    _format_watchlist(ws, len(rows))

    # Live signals tab
    sg_headers = ["Symbol", "Strategy", "Entry", "Stop", "Target", "Score", "Dividend", "Reason"]
    ws2 = _ensure_ws(sh, "LiveSignals", sg_headers)
    sg_keys = ["symbol", "strategy", "entry", "stop", "target", "score", "dividend", "reason"]
    rows2 = [[("YES" if s.get(k) else ("" if k == "dividend" else _native(s.get(k)))) if k == "dividend"
              else _native(s.get(k)) for k in sg_keys] for s in result["live_signals"]]
    if rows2:
        ws2.update(values=rows2, range_name="A3")
    _format_signals(ws2, len(rows2))

    print(f"  (gsheets) pushed {len(rows)} rankings, {len(rows2)} live signals.")
    return True


def push_positions() -> bool:
    """Write current Alpaca positions (with stops/targets/health) to a Positions tab."""
    if not settings.gsheet_id:
        return False
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        tc = TradingClient(settings.api_key, settings.api_secret, paper=not settings.live_trading)
        positions = tc.get_all_positions()
        open_orders = tc.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))

        # Build stop/target lookup. After entry fills, bracket legs become standalone
        # orders — check both leg lists and top-level order type.
        stops: dict = {}
        targets: dict = {}
        for o in open_orders:
            sym = o.symbol
            otype = str(o.type).lower()
            if o.legs:
                for leg in o.legs:
                    ltype = str(leg.type).lower()
                    if "stop" in ltype and leg.stop_price:
                        stops[sym] = float(leg.stop_price)
                    elif "limit" in ltype and "stop" not in ltype and leg.limit_price:
                        targets[sym] = float(leg.limit_price)
            else:
                if "stop" in otype and o.stop_price:
                    stops[sym] = float(o.stop_price)
                elif otype == "limit" and o.limit_price:
                    targets[sym] = float(o.limit_price)

        sh = _client().open_by_key(settings.gsheet_id)
        pos_headers = ["Symbol", "Shares", "Entry", "Current", "P&L $", "P&L %", "Stop", "Target", "Health"]
        ws = _ensure_ws(sh, "Positions", pos_headers)

        rows = []
        for p in sorted(positions, key=lambda x: x.symbol):
            sym = p.symbol
            entry = float(p.avg_entry_price)
            current = float(p.current_price)
            pnl_dollars = float(p.unrealized_pl)
            pnl_pct = round(float(p.unrealized_plpc) * 100, 2)
            stop = stops.get(sym)
            target = targets.get(sym)

            # Trade health: R-multiple from entry toward stop/target
            if stop and stop != entry:
                r = round((current - entry) / (entry - stop), 2)
                if r >= 1.5:
                    health = f"+{r}R STRONG"
                elif r > 0:
                    health = f"+{r}R Winning"
                elif r == 0:
                    health = "Breakeven"
                else:
                    health = f"{r}R At Risk"
            else:
                label = "Winning" if pnl_pct > 0 else ("Breakeven" if pnl_pct == 0 else "At Risk")
                health = f"{label} (no stop)"

            rows.append([
                sym,
                int(float(p.qty)),
                round(entry, 2),
                round(current, 2),
                round(pnl_dollars, 2),
                f"{'+' if pnl_pct >= 0 else ''}{pnl_pct}%",
                round(stop, 2) if stop is not None else "",
                round(target, 2) if target is not None else "",
                health,
            ])

        if rows:
            ws.update(values=rows, range_name="A3")
        _format_positions(ws, len(rows))
        print(f"  (gsheets) pushed {len(rows)} positions.")
        return True
    except Exception as e:
        print(f"  (gsheets) positions skipped: {e}")
        return False


def push_daily_trades() -> bool:
    """Write today's approved trades (status=submitted) to a DailyTrades tab."""
    if not settings.gsheet_id:
        return False
    try:
        from datetime import date
        from .journal import fetch_approvals

        today = date.today().isoformat()
        approvals = fetch_approvals(limit=200)
        todays = [
            a for a in approvals
            if a.get("status") == "submitted" and (a.get("decided_ts") or "").startswith(today)
        ]

        sh = _client().open_by_key(settings.gsheet_id)
        headers = ["Symbol", "Strategy", "Qty", "Entry", "Stop", "Target", "$ Risk", "Time"]
        ws = _ensure_ws(sh, "DailyTrades", headers)

        rows = []
        for a in sorted(todays, key=lambda x: x.get("decided_ts") or ""):
            ts = a.get("decided_ts") or ""
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(ts).astimezone()
                time_str = dt.strftime("%I:%M %p")
            except Exception:
                time_str = ts[:16]
            rows.append([
                a["symbol"],
                a["strategy"],
                a["qty"],
                round(float(a["entry"]), 2),
                round(float(a["stop"]), 2),
                round(float(a["target"]), 2),
                round(float(a["dollar_risk"]), 2),
                time_str,
            ])

        if rows:
            ws.update(values=rows, range_name="A3")
        _format_daily_trades(ws, len(rows))
        print(f"  (gsheets) pushed {len(rows)} daily trades.")
        return True
    except Exception as e:
        print(f"  (gsheets) daily trades skipped: {e}")
        return False
