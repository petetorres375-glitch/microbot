"""
One-shot SPCX re-entry for Monday 2026-06-16.
Runs at 9:31 AM ET via cron. Places a GTC bracket if SPCX opens at or below $163.
Deletes itself from crontab after running.
"""
import subprocess
import sys
from datetime import date

from microbot.broker import Broker
from microbot import journal, notify
from alpaca.trading.requests import LimitOrderRequest, TakeProfitRequest, StopLossRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

SYMBOL = "SPCX"
ENTRY_LIMIT = 163.00
STOP = 150.00
TARGET = 189.00
QTY = 3

def remove_self_from_cron():
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    lines = [l for l in result.stdout.splitlines() if "spcx_monday.py" not in l]
    new_cron = "\n".join(lines) + "\n"
    subprocess.run(["crontab", "-"], input=new_cron, text=True)

def main():
    today = date.today().isoformat()
    if today != "2026-06-16":
        print(f"spcx_monday: skipping, today is {today} not 2026-06-16")
        remove_self_from_cron()
        return

    b = Broker()

    # Check current price
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestTradeRequest
        from microbot.config import settings
        data_client = StockHistoricalDataClient(settings.alpaca_api_key, settings.alpaca_api_secret)
        trade = data_client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=SYMBOL))[SYMBOL]
        price = float(trade.price)
        print(f"spcx_monday: {SYMBOL} latest trade ${price:.2f}")
    except Exception as e:
        print(f"spcx_monday: could not get price ({e}), proceeding with limit order anyway")
        price = ENTRY_LIMIT

    if price > ENTRY_LIMIT * 1.02:  # gapped up more than 2% above limit — skip
        msg = f"spcx_monday: {SYMBOL} opened at ${price:.2f}, above limit ${ENTRY_LIMIT} — skipping"
        print(msg)
        notify.notify(msg)
        remove_self_from_cron()
        return

    # Place GTC bracket
    try:
        order = b.client.submit_order(LimitOrderRequest(
            symbol=SYMBOL,
            qty=QTY,
            side=OrderSide.BUY,
            type="limit",
            limit_price=ENTRY_LIMIT,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=TARGET),
            stop_loss=StopLossRequest(stop_price=STOP),
        ))
        msg = (f"spcx_monday: placed {QTY}x {SYMBOL} limit ${ENTRY_LIMIT} "
               f"stop ${STOP} target ${TARGET} — order {order.id}")
        print(msg)
        notify.notify(msg)

        journal.init()
        journal.log_signal(SYMBOL, "ipo_manual", QTY, ENTRY_LIMIT, STOP, TARGET,
                           f"SPCX day-2 re-entry, limit ${ENTRY_LIMIT}", score=0.0)
    except Exception as e:
        msg = f"spcx_monday: order failed — {e}"
        print(msg)
        notify.notify(msg)

    remove_self_from_cron()

if __name__ == "__main__":
    main()
