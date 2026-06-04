"""
rebalance.py
------------
Rebalance the portfolio to a target set of symbols in one command.

    python rebalance.py --target IREN,LEGN,LUNR,TGTX,KEEL
    python rebalance.py --target IREN,LEGN,LUNR,TGTX,KEEL --dry-run

What it does:
  1. Fetches current Alpaca positions.
  2. Closes anything NOT in --target (cancels bracket legs first, then closes).
  3. For each symbol IN --target that you don't hold yet:
       - Runs the scanner to look for a live signal.
       - If a signal exists: shows entry/stop/target/sizing and asks Y/n.
       - If no signal: asks for your stop price, calculates the rest, asks Y/n.
  4. Marks manually-closed positions as closed in the journal.
"""
from __future__ import annotations

import argparse
import os
import time

from dotenv import load_dotenv

load_dotenv()

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest, TakeProfitRequest, StopLossRequest
from alpaca.trading.enums import QueryOrderStatus, OrderSide, TimeInForce, OrderClass

from microbot.broker import Broker
from microbot import journal
from microbot.screener import research
from microbot.config import settings


def _cancel_symbol_orders(broker: Broker, symbol: str) -> int:
    orders = broker.client.get_orders(filter=GetOrdersRequest(
        status=QueryOrderStatus.OPEN, symbols=[symbol], limit=50
    ))
    cancelled = 0
    for o in orders:
        try:
            broker.client.cancel_order_by_id(o.id)
            cancelled += 1
        except Exception as e:
            print(f"    warning: could not cancel {o.id}: {e}")
    return cancelled


def _close_position(broker: Broker, symbol: str, current_price: float, dry_run: bool):
    if dry_run:
        print(f"  [dry-run] would close {symbol} @ ~${current_price:.2f}")
        return

    n = _cancel_symbol_orders(broker, symbol)
    if n:
        print(f"  cancelled {n} open order(s) for {symbol}")
        time.sleep(0.6)

    try:
        resp = broker.client.close_position(symbol)
        print(f"  ✓ close submitted: {resp.id}")
        _journal_manual_close(symbol, current_price)
    except Exception as e:
        print(f"  ✗ close failed: {e}")


def _journal_manual_close(symbol: str, exit_price: float):
    """Mark open journal orders for this symbol closed and log a manual trade."""
    import sqlite3
    from microbot import journal as j
    open_orders = [r for r in j.fetch_open_orders() if r["symbol"] == symbol]
    for row in open_orders:
        entry = float(row["entry"])
        stop = float(row["stop"])
        qty = int(row["qty"])
        pnl = (exit_price - entry) * qty
        risk = entry - stop
        r_mult = (exit_price - entry) / risk if risk > 0 else 0.0
        j.log_closed_trade({
            "symbol": symbol, "strategy": row["strategy"], "qty": qty,
            "entry": entry, "exit": round(exit_price, 4),
            "outcome": "manual", "pnl": round(pnl, 2), "r_multiple": round(r_mult, 3),
        })
        j.mark_order_closed(row["order_id"])
    if open_orders:
        print(f"  journal: marked {len(open_orders)} order(s) closed for {symbol}")


def _get_mid_price(symbol: str) -> float | None:
    try:
        data_client = StockHistoricalDataClient(settings.api_key, settings.api_secret)
        q = data_client.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=symbol)
        )[symbol]
        return (float(q.ask_price) + float(q.bid_price)) / 2
    except Exception:
        return None


def _place_bracket(broker: Broker, symbol: str, qty: int,
                   stop: float, target: float, dry_run: bool) -> bool:
    if dry_run:
        print(f"  [dry-run] would submit bracket: {qty} shares, stop={stop:.2f}, target={target:.2f}")
        return True
    try:
        order = broker.client.submit_order(MarketOrderRequest(
            symbol=symbol, qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(target, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop, 2)),
        ))
        print(f"  ✓ bracket submitted: {order.id}")
        return True
    except Exception as e:
        print(f"  ✗ order failed: {e}")
        return False


def _confirm(prompt: str) -> bool:
    try:
        ans = input(prompt).strip().lower()
        return ans in ("", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def main():
    parser = argparse.ArgumentParser(description="Rebalance portfolio to a target symbol list")
    parser.add_argument("--target", required=True,
                        help="Comma-separated target symbols, e.g. IREN,LEGN,LUNR")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen without placing any orders")
    args = parser.parse_args()

    target = {s.strip().upper() for s in args.target.split(",") if s.strip()}
    dry_run = args.dry_run

    journal.init()
    broker = Broker()
    mode = "PAPER" if broker.paper else "LIVE"
    print(f"[{mode}]{' DRY-RUN' if dry_run else ''} rebalancing → {sorted(target)}\n")

    current = {p["symbol"]: p for p in broker.positions()}
    to_close = sorted(sym for sym in current if sym not in target)
    to_buy   = sorted(sym for sym in target if sym not in current)
    to_keep  = sorted(sym for sym in target if sym in current)

    # ── summary ────────────────────────────────────────────────────────────────
    if to_keep:
        print(f"Keep ({len(to_keep)}): {', '.join(to_keep)}")
    if to_close:
        print(f"Close ({len(to_close)}): {', '.join(to_close)}")
    if to_buy:
        print(f"Buy ({len(to_buy)}): {', '.join(to_buy)}")
    print()

    # ── closes ─────────────────────────────────────────────────────────────────
    for sym in to_close:
        price = current[sym]["current_price"]
        print(f"Closing {sym} @ ${price:.2f} (P&L ${current[sym]['unrealized_pl']:.2f})")
        _close_position(broker, sym, price, dry_run)

    if to_close and to_buy:
        print()

    # ── buys ───────────────────────────────────────────────────────────────────
    if to_buy:
        print(f"Scanning {to_buy} for signals...")
        result = research(universe=to_buy)
        signals = {s["symbol"]: s for s in result["live_signals"]}
        best_rank: dict[str, dict] = {}
        for r in result["rankings"]:
            sym = r["symbol"]
            if sym not in to_buy or r["trades"] < 4:
                continue
            prev = best_rank.get(sym)
            if prev is None or r["expectancy_r"] > prev["expectancy_r"]:
                best_rank[sym] = r
        print()

        account = broker.account()
        equity = account["equity"]
        risk_budget = settings.starting_equity * 0.01

        for sym in to_buy:
            print(f"── {sym} ──")
            mid = _get_mid_price(sym)

            if sym in signals:
                sig = signals[sym]
                entry    = float(sig["entry"])
                stop     = float(sig["stop"])
                tgt      = float(sig["target"])
                strategy = sig["strategy"]
                reason   = sig.get("reason", "")
                print(f"  live signal: {strategy} — {reason}")
            else:
                print("  no live signal today")
                if sym in best_rank:
                    r = best_rank[sym]
                    print(f"  best backtest: {r['strategy']} "
                          f"({r['trades']} trades, {r['win_rate']:.0%} WR, "
                          f"{r['expectancy_r']:.3f}R, maxDD={r['max_dd_R']}R)")
                if mid:
                    print(f"  current mid: ${mid:.2f}")
                try:
                    raw = input(f"  Stop price for {sym} (blank to skip): ").strip()
                except (EOFError, KeyboardInterrupt):
                    raw = ""
                if not raw:
                    print(f"  skipped {sym}\n")
                    continue
                stop = float(raw)
                entry = mid or stop * 1.05
                tgt = round(entry + 2 * (entry - stop), 2)

            risk_per_share = entry - stop
            if risk_per_share <= 0:
                print(f"  ✗ stop ({stop:.2f}) >= entry ({entry:.2f}), skipping\n")
                continue

            qty = int(risk_budget / risk_per_share)
            if qty < 1:
                print(f"  ✗ 1 share risks ${risk_per_share:.2f} > ${risk_budget:.2f} budget, skipping\n")
                continue

            rr = (tgt - entry) / risk_per_share
            dollar_risk = qty * risk_per_share
            print(f"  entry ~${entry:.2f}  stop ${stop:.2f}  target ${tgt:.2f}  "
                  f"R:R={rr:.1f}:1")
            print(f"  {qty} shares × ${entry:.2f} = ${qty*entry:.0f} position  "
                  f"risk=${dollar_risk:.2f}")

            if _confirm(f"  Place bracket for {qty} × {sym}? [Y/n]: "):
                _place_bracket(broker, sym, qty, stop, tgt, dry_run)
            else:
                print(f"  skipped")
            print()

    # ── final state ────────────────────────────────────────────────────────────
    if not dry_run:
        time.sleep(1)
        print("\nFinal positions:")
        for p in broker.positions():
            pl = p["unrealized_pl"]
            pct = p["unrealized_plpc"] * 100
            print(f"  {p['symbol']:<6} {int(p['qty']):>3} shares @ "
                  f"${p['avg_entry']:.2f}  current ${p['current_price']:.2f}  "
                  f"P&L ${pl:+.2f} ({pct:+.1f}%)")


if __name__ == "__main__":
    main()
