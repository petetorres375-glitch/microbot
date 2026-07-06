"""
check_positions.py
-------------------
Prints a snapshot of all open Alpaca positions with live P/L against
settings.starting_equity (not the inflated paper account balance).
Read-only — places no orders, safe to run on any cadence.
"""
from microbot.broker import Broker
from microbot.config import settings


def main():
    b = Broker()
    positions = b.client.get_all_positions()
    if not positions:
        print("No open positions.")
        return

    total = 0.0
    for p in positions:
        pl = float(p.unrealized_pl)
        total += pl
        print(f"{p.symbol}: {p.qty} shares @ avg {p.avg_entry_price}, "
              f"current {p.current_price}, unrealized P/L ${pl:.2f} "
              f"({float(p.unrealized_plpc) * 100:.2f}%)")

    pct_of_equity = total / settings.starting_equity * 100
    print(f"Total unrealized: ${total:.2f} ({pct_of_equity:.2f}% of "
          f"${settings.starting_equity:,.0f} starting equity)")


if __name__ == "__main__":
    main()
