"""One-shot: market sell 49 F at open (2026-06-16 9:31 AM ET). Self-removes from cron."""
import os
import subprocess
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

load_dotenv()
client = TradingClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_API_SECRET"], paper=True)

try:
    order = client.submit_order(MarketOrderRequest(
        symbol="F",
        qty=49,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    ))
    print(f"[sell_f_oneshot] submitted: {order.symbol} qty={order.qty} status={order.status} id={order.id}")
except Exception as e:
    print(f"[sell_f_oneshot] ERROR: {e}")

# Self-remove from crontab
result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
new_crontab = "\n".join(
    line for line in result.stdout.splitlines()
    if "sell_f_oneshot" not in line
) + "\n"
subprocess.run(["crontab", "-"], input=new_crontab, text=True)
print("[sell_f_oneshot] removed from crontab")
