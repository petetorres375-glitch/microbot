"""
Full pre-market readiness check — runs at 7:30 AM ET.
Covers everything: credentials, Alpaca, positions/stops, verdicts, DB, git.
Exits 0 if GO, 1 if any critical issue found.

Run standalone: python -m microbot.premarket_check
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from datetime import date

from .config import settings
from . import journal

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"
INFO = "ℹ️"


def _section(title: str):
    print(f"\n── {title} {'─' * max(0, 40 - len(title))}")


def run() -> bool:
    critical: list[str] = []
    warnings: list[str] = []

    print("=" * 50)
    print("  microbot PRE-MARKET READINESS CHECK")
    print(f"  {date.today()}  |  target open: 9:30 AM ET")
    print("=" * 50)

    # ── 1. Credentials ────────────────────────────────
    _section("Credentials")
    if not settings.api_key or not settings.api_secret:
        print(f"  {FAIL} ALPACA_API_KEY / ALPACA_API_SECRET missing")
        critical.append("credentials")
    else:
        print(f"  {PASS} API credentials present")
    mode = "LIVE" if settings.live_trading else "PAPER"
    print(f"  {INFO} Mode: {mode}  |  starting equity: ${settings.starting_equity:,.0f}  |  max positions: {settings.max_open_positions}")

    # ── 2. Alpaca connection ──────────────────────────
    _section("Alpaca Connection")
    client = None
    positions = []
    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(settings.api_key, settings.api_secret,
                               paper=not settings.live_trading)
        acct = client.get_account()
        equity = float(acct.equity)
        cash = float(acct.cash)
        bp = float(acct.buying_power)
        print(f"  {PASS} Connected [{mode}] — equity=${equity:,.2f}  cash=${cash:,.2f}  buying_power=${bp:,.2f}")
        if equity < 1000:
            print(f"  {WARN} Equity is low (${equity:,.2f})")
            warnings.append("low_equity")
    except Exception as exc:
        print(f"  {FAIL} Connection failed: {exc}")
        critical.append("alpaca_connect")

    # ── 3. Open positions + stop audit ───────────────
    _section("Open Positions & Stop Audit")
    if client:
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            positions = client.get_all_positions()
            n = len(positions)
            mx = settings.max_open_positions
            print(f"  {INFO} {n}/{mx} positions open")

            # Original stop per symbol, from the journal — trail.py deliberately
            # never updates this so R-multiples keep the ORIGINAL-risk
            # denominator. Using the live (possibly trail-ratcheted) stop here
            # instead flips the sign once a stop is raised above entry to lock
            # in profit, showing a nonsensical negative R on a winning position.
            original_stops = {o["symbol"]: o["stop"] for o in journal.fetch_open_orders()}

            for p in positions:
                sym = p.symbol
                entry = float(p.avg_entry_price)
                current = float(p.current_price)
                pnl_pct = float(p.unrealized_plpc) * 100

                # Check for active stop order
                req = GetOrdersRequest(status=QueryOrderStatus.ALL,
                                       symbols=[sym], limit=10)
                sym_orders = client.get_orders(filter=req)
                active_stops = [
                    o for o in sym_orders
                    if o.type.value == "stop"
                    and o.side.value == "sell"
                    and o.status.value in ("new", "held", "accepted", "pending_new")
                ]
                if active_stops:
                    stop_px = float(active_stops[0].stop_price)
                    risk_stop = original_stops.get(sym, stop_px)
                    risk_r = (current - entry) / (entry - risk_stop) if entry != risk_stop else 0
                    print(f"  {PASS} {sym}: entry=${entry:.2f}  now=${current:.2f} ({pnl_pct:+.1f}%)  stop=${stop_px:.2f}  R={risk_r:+.2f}")
                else:
                    print(f"  {FAIL} {sym}: entry=${entry:.2f}  now=${current:.2f} ({pnl_pct:+.1f}%)  — NO ACTIVE STOP FOUND")
                    critical.append(f"no_stop_{sym}")

        except Exception as exc:
            print(f"  {WARN} Position audit failed: {exc}")
            warnings.append("position_audit")
    else:
        print(f"  {WARN} Skipped — Alpaca not connected")

    # ── 4. Morning verdicts ───────────────────────────
    _section("Morning Verdicts")
    try:
        with open("morning_verdicts.json") as f:
            raw = json.load(f)
        verdict_date = raw.get("date", "")
        today = str(date.today())
        verdicts = raw.get("verdicts", {})
        clean = sum(1 for v in verdicts.values() if v == "CLEAN")
        caution = sum(1 for v in verdicts.values() if v == "CAUTION")
        avoid = sum(1 for v in verdicts.values() if v == "AVOID")
        if verdict_date == today:
            print(f"  {PASS} Fresh ({verdict_date}) — {len(verdicts)} symbols: {clean} CLEAN / {caution} CAUTION / {avoid} AVOID")
        else:
            from datetime import datetime, timezone, timedelta
            et_hour = datetime.now(timezone(timedelta(hours=-4))).hour
            if et_hour < 9:
                # Before 9 AM — morning analysis hasn't fired yet, this is expected
                print(f"  {INFO} Yesterday's verdicts on file ({verdict_date}) — fresh verdicts expected after 8:30 AM analysis")
            else:
                print(f"  {WARN} Stale (dated {verdict_date}, today={today}) — morning analysis push may have failed")
                print(f"       Engine will skip all signals until fresh verdicts are pushed")
                warnings.append("stale_verdicts")
    except FileNotFoundError:
        print(f"  {WARN} morning_verdicts.json not found — engine will skip all signals today")
        warnings.append("no_verdicts")
    except Exception as exc:
        print(f"  {WARN} Verdicts check error: {exc}")

    # ── 5. Database ───────────────────────────────────
    _section("Database")
    try:
        conn = sqlite3.connect(settings.db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        required = {"orders", "trades", "signals"}
        missing = required - tables
        if missing:
            print(f"  {FAIL} Missing tables: {missing}")
            critical.append("db_missing_tables")
        else:
            open_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE closed=0").fetchone()[0]
            closed_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            intraday = conn.execute("SELECT COUNT(*) FROM intraday_trades").fetchone()[0]
            print(f"  {PASS} DB healthy — {open_orders} open orders | {closed_trades} closed swing trades | {intraday} intraday trades")

            # Flag any journal orders with fake alpaca IDs that won't reconcile
            bad_ids = conn.execute(
                "SELECT symbol FROM orders WHERE closed=0 AND (alpaca_id LIKE 'rebalance-%' OR alpaca_id LIKE 'OrderStatus.%')"
            ).fetchall()
            if bad_ids:
                syms = [r[0] for r in bad_ids]
                print(f"  {WARN} Journal has non-UUID alpaca_ids for: {syms} — reconcile won't auto-close these")
                warnings.append("bad_alpaca_ids")
        conn.close()
    except Exception as exc:
        print(f"  {FAIL} DB error: {exc}")
        critical.append("database")

    # ── 6. Git repo ───────────────────────────────────
    _section("Git Repo")
    try:
        result = subprocess.run(["git", "fetch", "origin"], capture_output=True, text=True, timeout=15)
        status = subprocess.run(["git", "status", "-sb"], capture_output=True, text=True)
        log = subprocess.run(["git", "log", "--oneline", "-3"], capture_output=True, text=True)
        print(f"  {PASS} Repo accessible")
        print(f"  {INFO} Status: {status.stdout.strip().splitlines()[0]}")
        for line in log.stdout.strip().splitlines():
            print(f"  {INFO} {line}")
    except Exception as exc:
        print(f"  {WARN} Git check failed: {exc}")
        warnings.append("git")

    # ── 7. Core module imports ────────────────────────
    _section("Core Modules")
    try:
        from .strategies import build_strategies_from_params  # noqa: F401
        from .screener import research  # noqa: F401
        from .intraday_scanner import scan  # noqa: F401
        print(f"  {PASS} strategies, screener, intraday_scanner all import cleanly")
    except Exception as exc:
        print(f"  {FAIL} Import error: {exc}")
        critical.append("imports")

    # ── Final verdict ─────────────────────────────────
    print("\n" + "=" * 50)
    if critical:
        print(f"  {FAIL} NO-GO — {len(critical)} critical issue(s): {critical}")
        if warnings:
            print(f"  {WARN} Also {len(warnings)} warning(s): {warnings}")
        print("  Fix before 9:15 AM or routines will abort on their own diagnostics.")
    else:
        print(f"  {PASS} GO — all critical checks passed")
        if warnings:
            print(f"  {WARN} {len(warnings)} warning(s) to review: {warnings}")
        print("  Routines will fire normally at 9:15 AM / 9:35 AM ET.")
    print("=" * 50 + "\n")

    return len(critical) == 0


if __name__ == "__main__":
    import sys
    ok = run()
    sys.exit(0 if ok else 1)
