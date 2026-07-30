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

Every tab push (_push_tab) issues exactly 2 Sheets API write calls total — one
values.batchUpdate (timestamp + headers + data + guide) and one spreadsheets.batchUpdate
(unmerge + freeze + merges + all cell formatting + column widths + row height) — instead
of formatting each header cell / guide section / row band with its own individual call.
That used to add up to ~20-25 calls per tab and reliably blew through the Sheets API's
60-writes/minute quota across a 4-tab research push; batching everything that CAN be
batched into single calls keeps a full run (Watchlist + LiveSignals + Positions +
DailyTrades) at ~8 calls total, comfortably under quota without needing long sleeps.
"""
from __future__ import annotations

import time
from typing import Dict, List

from gspread.utils import a1_range_to_grid_range

from .config import settings


def _retry_on_quota(fn, *, retries=2, wait=30):
    """Run fn(); on a Sheets 429 write-quota error, wait and retry before giving up."""
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as e:
            if "429" in str(e) and attempt < retries:
                time.sleep(wait)
                continue
            raise


def _spy_benchmark(lookback_days: int = 1100) -> str:
    """SPY buy-and-hold return over the backtest lookback window."""
    try:
        from .data import MarketData
        df = MarketData().bars("SPY", lookback_days=lookback_days)
        if df is None or len(df) < 2:
            return ""
        ret = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
        years = round(len(df) / 252, 1)
        return f"SPY B&H {years}yr: {ret:+.1f}%"
    except Exception:
        return ""

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


def _col_letter(n: int) -> str:
    """Convert 1-based column index to letter (1→A, 2→B …)."""
    return chr(ord("A") + n - 1)


def _guide_start_row(data_rows: int) -> int:
    return data_rows + 4  # +1 timestamp row, +1 header row, +1 gap, +1


def _guide_values(guide: list, col_count: int) -> List[List]:
    values = [["Column Guide"] + [""] * (col_count - 1)]
    values += [[label, desc] + [""] * (col_count - 2) for label, desc in guide]
    return values


# ---- raw batchUpdate request builders (build a dict/list only; no API call happens here) ----

def _grid_range(ws, a1_range: str) -> dict:
    return a1_range_to_grid_range(a1_range, ws.id)


def _fmt_req(ws, a1_range: str, style: dict) -> dict:
    return {
        "repeatCell": {
            "range": _grid_range(ws, a1_range),
            "cell": {"userEnteredFormat": style},
            "fields": "userEnteredFormat(%s)" % ",".join(style.keys()),
        }
    }


def _merge_req(ws, a1_range: str) -> dict:
    return {"mergeCells": {"range": _grid_range(ws, a1_range), "mergeType": "MERGE_ALL"}}


def _unmerge_req(ws) -> dict:
    """Clear all merges so stale guide/timestamp merges don't block data rows on the next write."""
    return {"unmergeCells": {"range": {
        "sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 200,
        "startColumnIndex": 0, "endColumnIndex": 26,
    }}}


def _freeze_req(ws, rows: int = 2) -> dict:
    return {"updateSheetProperties": {
        "properties": {"sheetId": ws.id, "gridProperties": {"frozenRowCount": rows}},
        "fields": "gridProperties.frozenRowCount",
    }}


def _col_width_reqs(ws, widths: List[int]) -> List[dict]:
    return [
        {"updateDimensionProperties": {
            "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w},
            "fields": "pixelSize",
        }}
        for i, w in enumerate(widths)
    ]


def _row_height_req(ws, start_row: int, end_row: int, height: int = 28) -> dict:
    return {"updateDimensionProperties": {
        "range": {"sheetId": ws.id, "dimension": "ROWS", "startIndex": start_row - 1, "endIndex": end_row},
        "properties": {"pixelSize": height},
        "fields": "pixelSize",
    }}


def _header_cell_reqs(ws, col_colors: List[dict], col_count: int) -> List[dict]:
    """Per-column header color (row 2) — each column has its own color, so each needs its own request."""
    reqs = []
    for i, bg in enumerate(col_colors[:col_count]):
        col = _col_letter(i + 1)
        reqs.append(_fmt_req(ws, f"{col}2", {
            "backgroundColor": bg,
            "textFormat": {"bold": True, "fontSize": 12, "foregroundColor": _DARK_TEXT},
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
            "borders": {"top": _BORDER, "bottom": _BORDER, "left": _BORDER, "right": _BORDER},
        }))
    return reqs


def _data_row_reqs(ws, row_count: int, col_count: int) -> List[dict]:
    """Alternating row banding for the data rows — one request per row (all bundled into the caller's single batch call)."""
    if row_count == 0:
        return []
    end_col = _col_letter(col_count)
    base_style = {
        "textFormat": {"fontSize": 12, "foregroundColor": _DARK_TEXT},
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
        "borders": {"top": _BORDER, "bottom": _BORDER, "left": _BORDER, "right": _BORDER},
    }
    reqs = []
    for i in range(3, row_count + 3):
        style = {**base_style, "backgroundColor": _ROW_ALT if i % 2 == 0 else _WHITE}
        reqs.append(_fmt_req(ws, f"A{i}:{end_col}{i}", style))
    return reqs


def _guide_reqs(ws, data_rows: int, guide: list, col_count: int) -> List[dict]:
    start = _guide_start_row(data_rows)
    end_col = _col_letter(col_count)
    n = len(guide)
    return [
        _merge_req(ws, f"A{start}:{end_col}{start}"),
        _fmt_req(ws, f"A{start}:{end_col}{start}", {
            "backgroundColor": _GUIDE_HEADER_BG,
            "textFormat": {"bold": True, "fontSize": 14, "foregroundColor": _GUIDE_HEADER_FG},
            "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
        }),
        _fmt_req(ws, f"A{start + 1}:A{start + n}", {
            "backgroundColor": _GUIDE_LABEL_BG,
            "textFormat": {"bold": True, "fontSize": 14, "foregroundColor": _GUIDE_LABEL_FG},
            "horizontalAlignment": "LEFT", "verticalAlignment": "MIDDLE",
        }),
        _fmt_req(ws, f"B{start + 1}:{end_col}{start + n}", {
            "backgroundColor": _GUIDE_LABEL_BG,
            "textFormat": {"fontSize": 14, "foregroundColor": _GUIDE_LABEL_FG},
            "horizontalAlignment": "LEFT", "verticalAlignment": "MIDDLE",
        }),
    ]


def _native(v):
    """Convert numpy scalars to plain Python types so gspread can serialize them."""
    try:
        return v.item()
    except AttributeError:
        return v


def _push_tab(sh, title: str, headers: List[str], rows: List[list],
              col_colors: List[dict], col_widths: List[int], guide: list) -> int:
    """Write one tab's timestamp/headers/data/guide + all formatting in exactly 2 API calls."""
    from datetime import datetime, timezone

    col_count = len(headers)
    try:
        ws = sh.worksheet(title)
    except Exception:
        ws = sh.add_worksheet(title=title, rows=200, cols=max(10, col_count))
    ws.clear()

    now = datetime.now(timezone.utc).astimezone()
    stamp = now.strftime("%B %d, %Y  %I:%M %p")
    stamp_row = [f"microbot  |  Last Updated: {stamp}  |  {_spy_benchmark(settings.lookback_days)}"] + [""] * (col_count - 1)

    row_count = len(rows)
    guide_start = _guide_start_row(row_count)

    value_ranges = [
        {"range": "A1", "values": [stamp_row]},
        {"range": "A2", "values": [headers]},
    ]
    if rows:
        value_ranges.append({"range": "A3", "values": rows})
    value_ranges.append({"range": f"A{guide_start}", "values": _guide_values(guide, col_count)})

    _retry_on_quota(lambda: ws.batch_update(value_ranges))

    requests = [_unmerge_req(ws), _freeze_req(ws, 2)]
    requests.append(_merge_req(ws, f"A1:{_col_letter(col_count)}1"))
    requests.append(_fmt_req(ws, f"A1:{_col_letter(col_count)}1", {
        "backgroundColor": _STAMP_BG,
        "textFormat": {"bold": True, "fontSize": 13, "foregroundColor": _STAMP_FG},
        "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
    }))
    requests += _header_cell_reqs(ws, col_colors, col_count)
    requests += _data_row_reqs(ws, row_count, col_count)
    requests += _col_width_reqs(ws, col_widths)
    requests.append(_row_height_req(ws, 1, max(row_count + 2, 3), 32))
    requests += _guide_reqs(ws, row_count, guide, col_count)

    _retry_on_quota(lambda: ws.spreadsheet.batch_update({"requests": requests}))

    return row_count


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
    n_rk = _push_tab(sh, "Watchlist", rk_headers, rows, _COL_COLORS_WL,
                      [218, 160, 80, 100, 120, 120, 110, 90, 80], _WL_GUIDE)

    # Live signals tab
    sg_headers = ["Symbol", "Strategy", "Entry", "Stop", "Target", "Score", "Dividend", "Reason"]
    sg_keys = ["symbol", "strategy", "entry", "stop", "target", "score", "dividend", "reason"]
    rows2 = [[("YES" if s.get(k) else ("" if k == "dividend" else _native(s.get(k)))) if k == "dividend"
              else _native(s.get(k)) for k in sg_keys] for s in result["live_signals"]]
    n_sg = _push_tab(sh, "LiveSignals", sg_headers, rows2, _COL_COLORS_SG,
                      [218, 160, 90, 90, 90, 90, 80, 320], _SG_GUIDE)

    print(f"  (gsheets) pushed {n_rk} rankings, {n_sg} live signals.")
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

        # Fall back to journal for any symbol missing stop/target (e.g. after hours
        # when bracket legs are not returned, or manually placed orders).
        from microbot.journal import fetch_open_orders, _conn as _jconn
        for row in fetch_open_orders():
            sym = row["symbol"]
            if sym not in stops and row.get("stop"):
                stops[sym] = float(row["stop"])
            if sym not in targets and row.get("target"):
                targets[sym] = float(row["target"])

        # Secondary fallback: approvals table (covers positions entered via engine approval gate).
        with _jconn() as con:
            for row in con.execute(
                "SELECT symbol, stop, target FROM approvals"
                " WHERE status='submitted' AND stop IS NOT NULL"
            ).fetchall():
                sym = row["symbol"]
                if sym not in stops and row["stop"]:
                    stops[sym] = float(row["stop"])
                if sym not in targets and row["target"]:
                    targets[sym] = float(row["target"])

        sh = _client().open_by_key(settings.gsheet_id)
        pos_headers = ["Symbol", "Shares", "Entry", "Current", "P&L $", "P&L %", "Stop", "Target", "Health"]

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

        n = _push_tab(sh, "Positions", pos_headers, rows, _COL_COLORS_POS,
                      [218, 80, 100, 100, 100, 100, 100, 100, 120], _POS_GUIDE)
        print(f"  (gsheets) pushed {n} positions.")
        return True
    except Exception as e:
        print(f"  (gsheets) positions skipped: {e}")
        return False


def push_daily_trades() -> bool:
    """Write today's submitted orders (CLEAN auto-executes + manual) to a DailyTrades tab."""
    if not settings.gsheet_id:
        return False
    try:
        from datetime import date
        from .journal import fetch_orders

        today = date.today().isoformat()
        orders = fetch_orders(limit=200)
        todays = [o for o in orders if (o.get("ts") or "").startswith(today)]

        sh = _client().open_by_key(settings.gsheet_id)
        headers = ["Symbol", "Strategy", "Qty", "Entry", "Stop", "Target", "$ Risk", "Time"]

        rows = []
        for o in sorted(todays, key=lambda x: x.get("ts") or ""):
            ts = o.get("ts") or ""
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(ts).astimezone()
                time_str = dt.strftime("%I:%M %p")
            except Exception:
                time_str = ts[:16]
            rows.append([
                o["symbol"],
                o["strategy"],
                o["qty"],
                round(float(o["entry"]), 2),
                round(float(o["stop"]), 2),
                round(float(o["target"]), 2),
                round(float(o["dollar_risk"]), 2),
                time_str,
            ])

        n = _push_tab(sh, "DailyTrades", headers, rows, _COL_COLORS_DT,
                      [218, 160, 70, 100, 100, 100, 90, 160], _DT_GUIDE)
        print(f"  (gsheets) pushed {n} daily trades.")
        return True
    except Exception as e:
        print(f"  (gsheets) daily trades skipped: {e}")
        return False
