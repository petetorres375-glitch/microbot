"""
enter_spcx.py
-------------
One-shot SPCX re-entry for 2026-06-17.
Polls for a real open print, buys 2 shares at market, places a GTC stop at
$190, and logs to the journal so the trail cron can ratchet.
"""
import sys
import time
from datetime import date

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, StopOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from microbot import journal
from microbot.config import settings

SYMBOL   = "SPCX"
QTY      = 2
STOP     = 190.00
TARGET   = 0.0   # trailing stop, no fixed target


def _real_price(data_client) -> float | None:
    """Return latest SPCX price only if it's a real trade (size > 0, today)."""
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
    if date.today() != date(2026, 6, 17):
        print(f"enter_spcx: date guard — today is {date.today()}, expected 2026-06-17. Exiting.")
        sys.exit(0)

    journal.init()

    trading    = TradingClient(settings.api_key, settings.api_secret,
                               paper=not settings.live_trading)
    data_client = StockHistoricalDataClient(settings.api_key, settings.api_secret)

    # Guard: skip if SPCX already held
    held = {p.symbol for p in trading.get_all_positions()}
    if SYMBOL in held:
        print(f"enter_spcx: {SYMBOL} already in portfolio — nothing to do.")
        sys.exit(0)

    # 1. Poll for a real print (IPO-day-one lesson: ghost quotes have size=0)
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

    # 2. Market buy
    buy = trading.submit_order(MarketOrderRequest(
        symbol=SYMBOL,
        qty=QTY,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    ))
    print(f"  Buy order submitted: {buy.id}")

    # 3. Poll for fill
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

    # 4. GTC stop at $190
    stop_order = trading.submit_order(StopOrderRequest(
        symbol=SYMBOL,
        qty=QTY,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC,
        stop_price=STOP,
    ))
    print(f"  GTC stop placed at ${STOP:.2f}: {stop_order.id}")

    # 5. Journal — trail cron reads entry+stop from here
    journal.log_manual_order(
        alpaca_id=str(buy.id),
        symbol=SYMBOL,
        qty=QTY,
        entry=round(fill, 2),
        stop=STOP,
        target=TARGET,
        strategy="manual",
    )
    risk = round(QTY * (fill - STOP), 2)
    print(f"  Journal: {SYMBOL} {QTY}sh @ ${fill:.2f}  stop=${STOP:.2f}  risk=${risk:.2f}")

    # Self-remove from crontab now that the job is done.
    import subprocess, re, pathlib
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    new_crontab = "\n".join(
        line for line in result.stdout.splitlines()
        if "enter_spcx" not in line
    ) + "\n"
    subprocess.run(["crontab", "-"], input=new_crontab, text=True)
    print("  Cron entry removed.")

    # Clean up CLAUDE.md: remove the enter_spcx cron line + its explanatory note,
    # and replace the "re-entry planned" paragraph with a simple "entered" line.
    claude_md = pathlib.Path(__file__).parent / "CLAUDE.md"
    text = claude_md.read_text()

    # Remove the one-shot cron line from the crontab block
    text = re.sub(r"\n31 9 17 6 \* cd .+enter_spcx\.py .+\n", "\n", text)
    # Remove the explanatory note that follows the crontab block
    text = re.sub(
        r"\nThe SPCX entry \(last line\) is a one-shot.+?Remove after 2026-06-17\.\n",
        "\n", text, flags=re.DOTALL,
    )
    # Replace the "re-entry planned" sentence with a simple "entered" record
    text = re.sub(
        r"SPCX re-entry planned 2026-06-17:.+?Trail cron ratchets once up ≥ 1R\.",
        f"SPCX: re-entered 2026-06-17 at ${fill:.2f}, 2 shares, stop $190 (trailing), no target, long-term hold.",
        text,
    )
    claude_md.write_text(text)
    print("  CLAUDE.md cleaned up.")

    # Commit and push so the repo stays current.
    repo = pathlib.Path(__file__).parent
    subprocess.run(["git", "-C", str(repo), "add", "CLAUDE.md"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m",
         f"CLAUDE.md: SPCX entered 2026-06-17 at ${fill:.2f} — cron note removed"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "push"], check=True)
    print("  CLAUDE.md committed and pushed.")
    print("Done.")


if __name__ == "__main__":
    main()
