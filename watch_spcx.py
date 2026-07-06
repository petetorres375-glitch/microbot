"""
watch_spcx.py — SPCX re-entry watcher.

SPCX (personal long-term hold) stopped out 2026-07-01 at $155.44 (6 shares,
entry $163.62). Per user instruction: watch the price, and if it reclaims
the prior entry ($163.62), buy back in — confirms the pullback is over
rather than catching a falling knife.

Guards against a repeat of the 2026-06-30 duplicate-buy incident (SPCX
briefly hit 95 shares because two universes fired signals in the same
engine run, sized off the $100K paper acct.equity instead of starting_equity):
  - Single buy path, no multi-strategy engine involved — nothing else can
    fire a second signal on this symbol.
  - Sizes off settings.starting_equity, never acct.equity.
  - Skips if SPCX is already held (no duplicate buy).
  - PID lockfile (spcx_watch.lock) rejects a second concurrent run — covers
    a manual run overlapping a cron-scheduled one.
  - Only acts on a verified real print (size > 0, stamped today) — filters
    ghost/reference quotes that caused issues on IPO day.
  - Idempotent across repeated cron runs: once held, every later run is a
    no-op via the position guard above.

Sizing/exit mirror enter_spcx_jun30.py: size from settings.starting_equity
(not the $100K paper default), 5% GTC stop below fill via OCO bracket, wide
take-profit ceiling since trail.py is the real exit (ratchets once up >= 1R).
"""
import os
import sys
import time
from datetime import date

from dotenv import load_dotenv
load_dotenv()

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest, LimitOrderRequest, StopOrderRequest,
    TakeProfitRequest, StopLossRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
from alpaca.common.exceptions import APIError

from microbot import journal
from microbot.config import settings

SYMBOL       = "SPCX"
TRIGGER      = 163.62  # prior entry / stop-out price — reclaim confirms recovery
STOP_PCT     = 5.0     # % below fill
LOCK_FILE    = "spcx_watch.lock"


def _acquire_lock() -> bool:
    if os.path.exists(LOCK_FILE):
        try:
            pid = int(open(LOCK_FILE).read().strip())
            os.kill(pid, 0)
            print(f"watch_spcx: already running (pid {pid}) — exiting to avoid duplicate buy.")
            return False
        except (ProcessLookupError, PermissionError):
            pass  # stale lock — process is gone
        except (ValueError, OSError):
            pass  # unreadable lock file
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def _release_lock():
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass


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
    if not _acquire_lock():
        sys.exit(1)

    try:
        journal.init()

        trading     = TradingClient(settings.api_key, settings.api_secret,
                                    paper=not settings.live_trading)
        data_client = StockHistoricalDataClient(settings.api_key, settings.api_secret)

        held = {p.symbol for p in trading.get_all_positions()}
        if SYMBOL in held:
            print(f"watch_spcx: {SYMBOL} already held — nothing to do.")
            return

        price = _real_price(data_client)
        if price is None:
            print(f"watch_spcx: no verified trade for {SYMBOL} today — skipped.")
            return

        if price < TRIGGER:
            print(f"watch_spcx: {SYMBOL} ${price:.2f} < trigger ${TRIGGER:.2f} — waiting.")
            return

        print(f"watch_spcx: {SYMBOL} ${price:.2f} >= trigger ${TRIGGER:.2f} — reclaim confirmed, buying.")

        equity      = settings.starting_equity
        risk_budget = equity * 0.01
        stop_approx    = round(price * (1 - STOP_PCT / 100), 2)
        risk_per_share = price - stop_approx
        qty            = max(1, int(risk_budget / risk_per_share))
        print(f"  Sizing: {qty} shares | risk/share ${risk_per_share:.2f} | budget ${risk_budget:.2f}")

        buy = trading.submit_order(MarketOrderRequest(
            symbol=SYMBOL,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        ))
        print(f"  Buy order submitted: {buy.id}")

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

        # Alpaca's position bookkeeping can lag a market fill by a few
        # seconds; submitting the exit OCO too early raises "oco orders
        # must be exit orders". Wait for the position to actually appear
        # before placing it.
        for _ in range(10):
            if any(p.symbol == SYMBOL for p in trading.get_all_positions()):
                break
            time.sleep(2)
        else:
            print(f"  WARNING: {SYMBOL} position never appeared after fill — "
                  f"skipping OCO, check Alpaca manually.")
            return

        stop = round(fill * (1 - STOP_PCT / 100), 2)
        target = round(fill * 1.30, 2)
        oco = None
        for attempt in range(3):
            try:
                oco = trading.submit_order(LimitOrderRequest(
                    symbol=SYMBOL,
                    qty=qty,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.GTC,
                    order_class=OrderClass.OCO,
                    take_profit=TakeProfitRequest(limit_price=target),
                    stop_loss=StopLossRequest(stop_price=stop),
                ))
                break
            except APIError as e:
                print(f"  OCO attempt {attempt + 1} failed: {e}")
                time.sleep(3)

        if oco is not None:
            print(f"  OCO stop placed at ${stop:.2f}: {oco.id}")
        else:
            # Position is filled and otherwise unprotected — a plain stop
            # (no take-profit leg) is strictly better than nothing.
            print("  OCO failed after retries — falling back to a plain stop order.")
            fallback = trading.submit_order(StopOrderRequest(
                symbol=SYMBOL,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                stop_price=stop,
            ))
            print(f"  Fallback stop placed at ${stop:.2f}: {fallback.id} "
                  f"(no take-profit leg — check Alpaca manually)")

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
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
