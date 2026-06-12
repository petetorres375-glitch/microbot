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

from datetime import date
from typing import List, Dict, Optional

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from alpaca.trading.enums import OrderSide, OrderType
from alpaca.trading.requests import ReplaceOrderRequest

from . import journal
from .config import settings


def _verified_price(data_client, symbol: str) -> Optional[float]:
    """Latest trade price, only if it's a real print: size > 0 and stamped
    today. Filters reference/ghost quotes (e.g. an IPO's indicative print with
    size 0), which would otherwise compute a bogus trail level."""
    try:
        t = data_client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=symbol))[symbol]
    except Exception:
        return None
    if not t or float(t.size or 0) <= 0:
        return None
    if t.timestamp.date() != date.today():
        return None
    return float(t.price)


def trail_swing_stops(broker, data_client=None) -> List[Dict]:
    """Raise stops on positions up >= 1R. Returns a list of adjustments made."""
    adjusted: List[Dict] = []
    positions = broker.positions()
    if not positions:
        return adjusted

    if data_client is None:
        data_client = StockHistoricalDataClient(
            settings.api_key, settings.api_secret)

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
        if rec is None or leg is None:
            continue
        price = _verified_price(data_client, sym)
        if price is None:
            print(f"  trail: no verified trade for {sym} today — skipped")
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


def main():
    """Standalone runner for the hourly market-hours cron. The today's-trade
    guard in _verified_price makes off-hours/holiday runs a clean no-op."""
    from datetime import datetime
    from .broker import Broker

    journal.init()
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] trail check")
    adjusted = trail_swing_stops(Broker())
    if not adjusted:
        print("  no adjustments")


if __name__ == "__main__":
    main()
