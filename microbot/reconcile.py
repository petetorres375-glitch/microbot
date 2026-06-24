"""
reconcile.py
------------
Turns placed orders into closed trades in the journal.

The engine logs an order the moment it submits the bracket (entry + stop +
target), but never goes back to ask Alpaca "did the stop or target fill?".
Until something does that, log_closed_trade() is never called in live
operation. This module is that something.

Per run:
  1. Ask the journal which entry orders are still open (logged, not yet closed).
  2. For each, fetch that order from Alpaca with its bracket legs nested.
  3. If a leg (take-profit or stop-loss) has filled, that's the exit.
  4. Compute realized P&L and the R-multiple from the entry and exit fills.
  5. Write a closed trade to the journal and mark the entry reconciled.

    python -m microbot.reconcile            # reconcile + write
    python -m microbot.reconcile --dry-run  # show what WOULD be written
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

from alpaca.trading.requests import GetOrderByIdRequest, GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus

from .broker import Broker
from . import journal
from . import notify


@dataclass
class ClosedTrade:
    order_id: str
    symbol: str
    strategy: str
    qty: int
    entry: float
    exit: float
    outcome: str   # "target" | "stop" | "manual" | "no_fill"
    pnl: float
    r_multiple: float

    def as_journal_row(self) -> dict:
        return {
            "symbol": self.symbol, "strategy": self.strategy, "qty": self.qty,
            "entry": round(self.entry, 4), "exit": round(self.exit, 4),
            "outcome": self.outcome, "pnl": round(self.pnl, 2),
            "r_multiple": round(self.r_multiple, 3),
        }


def _fetch_order_with_legs(broker: Broker, order_id: str):
    """Fetch the Alpaca order with bracket child legs attached, or None."""
    return broker.client.get_order_by_id(order_id, filter=GetOrderByIdRequest(nested=True))


def _leg_is_target(leg) -> bool:
    otype = str(getattr(leg, "order_type", None) or getattr(leg, "type", "")).lower()
    return "limit" in otype and "stop" not in otype


def _filled_price(order) -> float | None:
    p = getattr(order, "filled_avg_price", None)
    try:
        return float(p) if p is not None else None
    except (TypeError, ValueError):
        return None


def _status(order) -> str:
    s = getattr(order, "status", "")
    val = s.value if hasattr(s, "value") else str(s)
    return str(val).lower()


def _side(order) -> str:
    s = getattr(order, "side", "")
    val = s.value if hasattr(s, "value") else str(s)
    return str(val).lower()


def _find_filled_sell(broker: Broker, symbol: str) -> tuple[float, str] | None:
    """Search Alpaca's closed orders for the most recent filled sell on symbol.

    Used when a position is gone but no bracket leg shows as filled — e.g. when
    trail.py replaced the original bracket stop with a standalone stop/OCO order.
    Returns (exit_price, outcome) or None.
    """
    try:
        req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, symbols=[symbol], limit=20)
        orders = broker.client.get_orders(filter=req)
    except Exception:
        return None

    filled_sells = [
        o for o in orders
        if _side(o) == "sell" and _status(o) == "filled" and _filled_price(o) is not None
    ]
    if not filled_sells:
        return None

    # Most recent filled sell wins
    filled_sells.sort(
        key=lambda o: getattr(o, "filled_at", None) or getattr(o, "submitted_at", None),
        reverse=True,
    )
    best = filled_sells[0]
    price = _filled_price(best)
    otype = str(getattr(best, "order_type", "") or "").lower()
    outcome = "target" if ("limit" in otype and "stop" not in otype) else "stop"
    return price, outcome


def _build_closed_trade(open_row: dict, order) -> ClosedTrade | None:
    """Return a ClosedTrade if the bracket is done, or None if still live."""
    oid = open_row["order_id"]

    entry_fill = _filled_price(order)
    if entry_fill is None and _status(order) in {"canceled", "expired", "rejected"}:
        return ClosedTrade(oid, open_row["symbol"], open_row["strategy"],
                           int(open_row["qty"]), float(open_row["entry"]),
                           float(open_row["entry"]), "no_fill", 0.0, 0.0)
    if entry_fill is None:
        entry_fill = float(open_row["entry"])

    legs = list(getattr(order, "legs", None) or [])
    filled_exit = next((lg for lg in legs if _status(lg) == "filled"), None)
    if filled_exit is None:
        return None  # bracket still live

    exit_fill = _filled_price(filled_exit)
    if exit_fill is None:
        return None  # filled but no price yet; retry next run

    qty = int(getattr(filled_exit, "filled_qty", None) or open_row["qty"])
    outcome = "target" if _leg_is_target(filled_exit) else "stop"

    pnl = (exit_fill - entry_fill) * qty
    risk_per_share = float(open_row["entry"]) - float(open_row["stop"])
    r_multiple = (exit_fill - entry_fill) / risk_per_share if risk_per_share > 0 else 0.0

    return ClosedTrade(oid, open_row["symbol"], open_row["strategy"], qty,
                       entry_fill, exit_fill, outcome, pnl, r_multiple)


def reconcile_once(broker: Broker | None = None, dry_run: bool = False) -> list[ClosedTrade]:
    """Reconcile every open journal entry against Alpaca. Returns closed trades."""
    journal.init()
    broker = broker or Broker()

    open_orders = journal.fetch_open_orders()
    if not open_orders:
        print("No open orders in the journal to reconcile.")
        return []

    mode = "PAPER" if broker.paper else "LIVE"
    print(f"[{mode}] checking {len(open_orders)} open order(s)"
          f"{' (dry run)' if dry_run else ''}...")

    closed: list[ClosedTrade] = []
    for row in open_orders:
        oid = row["order_id"]
        try:
            order = _fetch_order_with_legs(broker, oid)
        except Exception as e:  # noqa: BLE001
            print(f"  ! could not fetch order {oid} ({row['symbol']}): {e}")
            continue
        if order is None:
            print(f"  ! order {oid} ({row['symbol']}) not found at broker")
            continue

        trade = _build_closed_trade(row, order)
        if trade is None:
            # Check if the position itself is gone from Alpaca (manual close)
            held = {p.symbol for p in broker.client.get_all_positions()}
            if row["symbol"] not in held and _status(order) == "filled":
                # Entry filled, position gone, no bracket leg filled.
                # Common cause: trail.py replaced the original bracket stop with a
                # standalone stop/OCO — that order won't appear as a bracket leg.
                # Search Alpaca's closed orders for the actual exit fill.
                exit_info = _find_filled_sell(broker, row["symbol"])
                if exit_info:
                    exit_price, outcome = exit_info
                    qty = int(row["qty"])
                    entry_price = float(row["entry"])
                    pnl = (exit_price - entry_price) * qty
                    risk = entry_price - float(row["stop"])
                    r_mult = (exit_price - entry_price) / risk if risk > 0 else 0.0
                    trade = ClosedTrade(
                        oid, row["symbol"], row["strategy"], qty,
                        entry_price, exit_price, outcome, pnl, r_mult,
                    )
                else:
                    trade = ClosedTrade(
                        oid, row["symbol"], row["strategy"], int(row["qty"]),
                        float(row["entry"]), float(row["entry"]),
                        "manual", 0.0, 0.0,
                    )
            else:
                print(f"  · {row['symbol']} still open")
                continue

        closed.append(trade)
        if dry_run:
            print(f"  WOULD record {trade.symbol} {trade.outcome} "
                  f"pnl=${trade.pnl:.2f} R={trade.r_multiple:+.2f}")
        else:
            journal.log_closed_trade(trade.as_journal_row())
            journal.mark_order_closed(oid)
            print(f"  ✓ recorded {trade.symbol} {trade.outcome} "
                  f"pnl=${trade.pnl:.2f} R={trade.r_multiple:+.2f}")

    real = [t for t in closed if t.outcome != "no_fill"]
    if real and not dry_run:
        total = sum(t.pnl for t in real)
        notify.notify(f"microbot reconciled {len(real)} closed trade(s), "
                      f"realized ${total:+.2f}. See the dashboard / run_analyzer.")
    return closed


def main():
    p = argparse.ArgumentParser(description="Reconcile closed Alpaca brackets into the journal")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would be recorded without writing to the journal")
    args = p.parse_args()
    reconcile_once(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
