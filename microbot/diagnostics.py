"""
Pre-run health check for microbot.
Call run() before the engine starts; it returns True if safe to proceed.
Run standalone: python -m microbot.diagnostics
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

from .config import settings

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"


def run() -> bool:
    """Run all checks. Returns True if all critical checks pass."""
    critical_failures: list[str] = []
    lines: list[str] = []

    # 1. API credentials present
    if not settings.api_key or not settings.api_secret:
        lines.append(f"{FAIL} ALPACA_API_KEY / ALPACA_API_SECRET missing from env")
        critical_failures.append("credentials")
    else:
        lines.append(f"{PASS} API credentials present")

    # 2. Alpaca connection + account
    client = None
    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(settings.api_key, settings.api_secret,
                               paper=not settings.live_trading)
        acct = client.get_account()
        equity = float(acct.equity)
        cash = float(acct.cash)
        mode = "PAPER" if not settings.live_trading else "LIVE"
        lines.append(f"{PASS} Alpaca [{mode}] connected — equity=${equity:,.2f}  cash=${cash:,.2f}")
        if equity < 500:
            lines.append(f"{WARN} Equity is very low (${equity:,.2f}) — check account")
    except Exception as exc:
        lines.append(f"{FAIL} Alpaca connection failed: {exc}")
        critical_failures.append("alpaca")

    # 3. Open positions vs limit
    if client:
        try:
            positions = client.get_all_positions()
            n = len(positions)
            mx = settings.max_open_positions
            sym_list = ", ".join(p.symbol for p in positions) or "none"
            if n >= mx:
                lines.append(f"{WARN} Positions {n}/{mx} (at max) — engine will skip new entries | {sym_list}")
            else:
                lines.append(f"{PASS} Positions {n}/{mx} open | {sym_list}")
        except Exception as exc:
            lines.append(f"{WARN} Could not fetch positions: {exc}")

    # 4. Morning verdicts
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
            lines.append(
                f"{PASS} Verdicts fresh ({verdict_date}) — "
                f"{len(verdicts)} symbols: {clean} CLEAN / {caution} CAUTION / {avoid} AVOID"
            )
        else:
            lines.append(
                f"{WARN} Verdicts stale (dated {verdict_date}, today={today}) — "
                f"engine will skip all signals"
            )
    except FileNotFoundError:
        lines.append(f"{WARN} morning_verdicts.json not found — engine will skip all signals")
    except Exception as exc:
        lines.append(f"{WARN} Verdicts check failed: {exc}")

    # 5. Database
    try:
        conn = sqlite3.connect(settings.db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        required = {"orders", "trades", "signals"}
        missing = required - tables
        if missing:
            lines.append(f"{FAIL} DB missing tables: {missing}")
            critical_failures.append("database")
        else:
            open_orders = conn.execute(
                "SELECT COUNT(*) FROM orders WHERE closed=0"
            ).fetchone()[0]
            closed_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            lines.append(
                f"{PASS} DB accessible — {open_orders} open orders, {closed_trades} closed trades"
            )
        conn.close()
    except Exception as exc:
        lines.append(f"{FAIL} DB check failed: {exc}")
        critical_failures.append("database")

    # 6. Key imports (strategies, screener)
    try:
        from .strategies import build_strategies_from_params  # noqa: F401
        from .screener import research  # noqa: F401
        lines.append(f"{PASS} Core modules import cleanly")
    except Exception as exc:
        lines.append(f"{FAIL} Module import error: {exc}")
        critical_failures.append("imports")

    # --- Report ---
    print("\n=== microbot diagnostics ===")
    for line in lines:
        print(f"  {line}")

    if critical_failures:
        print(f"\n{FAIL} DIAGNOSTICS FAILED — blocking issues: {critical_failures}")
        print("  Engine will NOT run.\n")
        return False

    print(f"\n{PASS} All checks passed — engine is go\n")
    return True


if __name__ == "__main__":
    import sys
    ok = run()
    sys.exit(0 if ok else 1)
