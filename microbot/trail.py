"""
trail.py
--------
Daily stop ratchet for swing positions — the swing-cadence equivalent of the
intraday 1R trail.

The swing engine runs once per day, so instead of tick-by-tick trailing this
ratchets at each run: any open position up at least 1R (original risk taken
from the journal's order record) gets its live stop order raised to lock in
50% of the current gain. Ratchet-only — a stop is never lowered. The 2:1
take-profit leg stays in place: the trail protects runners that stall under
target, it does not replace the target.

The journal's stored stop is deliberately NOT updated when trailing, so the
Sheets Health column keeps the original-risk denominator for its R-multiples.
"""
from __future__ import annotations

from typing import List, Dict

from alpaca.trading.enums import OrderSide, OrderType
from alpaca.trading.requests import ReplaceOrderRequest

from . import journal


def trail_swing_stops(broker) -> List[Dict]:
    """Raise stops on positions up >= 1R. Returns a list of adjustments made."""
    adjusted: List[Dict] = []
    positions = broker.positions()
    if not positions:
        return adjusted

    # Original entry/stop per symbol from the journal (latest open row wins).
    basis = {r["symbol"]: r for r in journal.fetch_open_orders()}

    # Live protective stop orders (bracket stop legs or standalone/OCO stops).
    stop_legs = {}
    try:
        for o in broker.open_orders():
            if (o.side == OrderSide.SELL
                    and o.type in (OrderType.STOP, OrderType.STOP_LIMIT)
                    and o.stop_price is not None):
                stop_legs[o.symbol] = o
    except Exception as e:
        print(f"  trail: could not list open orders ({e})")
        return adjusted

    for p in positions:
        sym = p["symbol"]
        rec = basis.get(sym)
        leg = stop_legs.get(sym)
        price = p["current_price"]
        if rec is None or leg is None or price <= 0:
            continue

        entry = float(rec["entry"])
        risk = entry - float(rec["stop"])
        gain = price - entry
        if risk <= 0 or gain < risk:
            continue  # not up 1R yet

        new_stop = round(entry + 0.5 * gain, 2)
        current_stop = float(leg.stop_price)
        if new_stop <= current_stop + 0.05:
            continue  # ratchet only, and skip sub-nickel churn

        try:
            broker.client.replace_order_by_id(
                str(leg.id), ReplaceOrderRequest(stop_price=new_stop))
            print(f"  TRAIL {sym}: stop {current_stop:.2f} → {new_stop:.2f} "
                  f"(+{gain / risk:.1f}R, entry {entry:.2f})")
            adjusted.append({"symbol": sym, "old_stop": current_stop,
                             "new_stop": new_stop, "r": round(gain / risk, 2)})
        except Exception as e:
            print(f"  trail failed {sym}: {e}")
    return adjusted
