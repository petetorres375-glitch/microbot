"""
enter_spcx_jun30.py — SPCX re-entry, June 30 2026.
Market buy sized to ~1% risk, GTC stop at 5% below fill.
No fixed target — trail.py ratchets once up >= 1R.
Run after 9:30 AM ET.
"""
import sys
import time
from datetime import date

from dotenv import load_dotenv
load_dotenv()

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, StopOrderRequest, LimitOrderRequest, TakeProfitRequest, StopLossRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

from microbot import journal
from microbot.config import settings

SYMBOL   = "SPCX"
STOP_PCT = 5.0   # % below fill


def _real_price(data_client) -> float | None:
    try:
        t = data_client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=SYMBOL))[SYMBOL]
    except Exception:
        return None
    if not t or float(t.size or 0) <= 0:
        return None
    if t.timestamp.date() != date.today():
        return None
    return float(t.price)


def main():
    if date.today() != date(2026, 6, 30):
        print(f"enter_spcx_jun30: date guard — today is {date.today()}, expected 2026-06-30. Exiting.")
        sys.exit(0)

    journal.init()

    trading     = TradingClient(settings.api_key, settings.api_secret,
                                paper=not settings.live_trading)
    data_client = StockHistoricalDataClient(settings.api_key, settings.api_secret)

    # Guard: skip if already held
    held = {p.symbol for p in trading.get_all_positions()}
    if SYMBOL in held:
        print(f"enter_spcx_jun30: {SYMBOL} already in portfolio — nothing to do.")
        sys.exit(0)

    equity       = settings.starting_equity
    risk_budget  = equity * 0.01
    print(f"Starting equity: ${equity:,.2f} | 1% risk budget: ${risk_budget:.2f}")

    # Poll for real print
    print(f"Polling {SYMBOL} for real print (max 5 min)...")
    price = None
    for attempt in range(30):
        price = _real_price(data_client)
        if price:
            print(f"  Real print: ${price:.2f}")
            break
        print(f"  [{attempt+1}/30] no real print yet — waiting 10s")
        time.sleep(10)

    if price is None:
        print("ERROR: no real print after 5 min — exiting without order.")
        sys.exit(1)

    # Size from actual price using starting_equity (not paper account $100K)
    stop_approx      = round(price * (1 - STOP_PCT / 100), 2)
    risk_per_share   = price - stop_approx
    qty              = max(1, int(risk_budget / risk_per_share))
    total_risk       = round(qty * risk_per_share, 2)
    print(f"  Sizing: {qty} shares | risk/share ${risk_per_share:.2f} | total risk ${total_risk:.2f} ({total_risk/equity*100:.1f}%)")

    # Market buy
    buy = trading.submit_order(MarketOrderRequest(
        symbol=SYMBOL,
        qty=qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    ))
    print(f"  Buy order submitted: {buy.id}")

    # Poll for fill
    fill = None
    for _ in range(30):
        time.sleep(2)
        o = trading.get_order_by_id(str(buy.id))
        if o.filled_avg_price is not None:
            fill = float(o.filled_avg_price)
            print(f"  Filled at ${fill:.2f}")
            break
    if fill is None:
        fill = price
        print(f"  Fill not confirmed — using poll price ${fill:.2f}")

    # GTC stop via OCO bracket (atomic — avoids wash-trade rejection on separate stop)
    stop = round(fill * (1 - STOP_PCT / 100), 2)
    oco = trading.submit_order(LimitOrderRequest(
        symbol=SYMBOL,
        qty=qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC,
        order_class=OrderClass.OCO,
        take_profit=TakeProfitRequest(limit_price=round(fill * 1.30, 2)),  # wide ceiling, trail is real exit
        stop_loss=StopLossRequest(stop_price=stop),
    ))
    print(f"  OCO stop placed at ${stop:.2f}: {oco.id}")

    # Journal — trail.py reads entry+stop from here to calculate 1R
    journal.log_manual_order(
        alpaca_id=str(buy.id),
        symbol=SYMBOL,
        qty=qty,
        entry=round(fill, 2),
        stop=stop,
        target=0.0,
        strategy="manual",
    )
    actual_risk = round(qty * (fill - stop), 2)
    print(f"  Journal: {SYMBOL} {qty}sh @ ${fill:.2f}  stop=${stop:.2f}  risk=${actual_risk:.2f}")
    print("Done. Trail cron ratchets once up >= 1R.")


if __name__ == "__main__":
    main()
