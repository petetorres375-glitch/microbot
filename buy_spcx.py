#!/usr/bin/env python3
"""
buy_spcx.py — buy 7 shares of SPCX at market, then place OCO stop + target.

Stop and target are calculated from the actual fill price, so safe to run
at any open price (IPO day-1 opens are unpredictable).

Usage:
    python buy_spcx.py             # buy 7 shares, 5% stop, 2:1 target
    python buy_spcx.py --stop-pct 7
    python buy_spcx.py --qty 5
    python buy_spcx.py --dry-run   # print intent, no orders placed

Run AFTER 9:30 AM ET. The buy order is DAY only — if not filled by close it cancels.
The OCO legs (stop + target) are GTC and survive overnight.
"""
import argparse
import time

from dotenv import load_dotenv
load_dotenv()

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest, LimitOrderRequest,
    TakeProfitRequest, StopLossRequest,
)
from alpaca.trading.enums import (
    OrderSide, OrderClass, TimeInForce, OrderStatus,
)

from microbot.config import settings

SYMBOL = "SPCX"
DEFAULT_QTY = 7
DEFAULT_STOP_PCT = 5.0  # % below fill; 5% on $135 ≈ $6.75/share, $47 total risk


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--qty", type=int, default=DEFAULT_QTY)
    parser.add_argument("--stop-pct", type=float, default=DEFAULT_STOP_PCT,
                        help="Stop-loss %% below fill price (default %(default)s%%)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    paper = not settings.live_trading
    client = TradingClient(settings.api_key, settings.api_secret, paper=paper)

    acct = client.get_account()
    equity = float(acct.equity)
    budget = equity * 0.01
    print(f"Account equity: ${equity:,.2f} | 1%% risk budget: ${budget:.2f}")
    print(f"{'[DRY RUN] ' if args.dry_run else ''}Buying {args.qty} shares of {SYMBOL} at market")
    print(f"Stop: {args.stop_pct}% below fill | Target: 2:1\n")

    if args.dry_run:
        approx_fill = 135.00
        stop = round(approx_fill * (1 - args.stop_pct / 100), 2)
        risk = approx_fill - stop
        target = round(approx_fill + 2 * risk, 2)
        print(f"[if fill ≈ $135]  stop=${stop:.2f}  target=${target:.2f}  risk/share=${risk:.2f}  total risk=${risk * args.qty:.2f}")
        return

    # --- place market buy ---
    buy = client.submit_order(MarketOrderRequest(
        symbol=SYMBOL,
        qty=args.qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    ))
    print(f"Buy order submitted: {buy.id}")

    # --- wait for fill (up to 5 minutes) ---
    print("Waiting for fill", end="", flush=True)
    fill_price = None
    for _ in range(60):
        time.sleep(5)
        o = client.get_order_by_id(str(buy.id))
        if o.status == OrderStatus.FILLED:
            fill_price = float(o.filled_avg_price)
            break
        print(".", end="", flush=True)

    if fill_price is None:
        print(f"\nNot filled after 5 min — check Alpaca dashboard for order {buy.id}")
        return

    print(f"\nFilled at ${fill_price:.2f}")

    # --- size stop + target from actual fill ---
    stop = round(fill_price * (1 - args.stop_pct / 100), 2)
    risk_per_share = fill_price - stop
    target = round(fill_price + 2 * risk_per_share, 2)
    total_risk = risk_per_share * args.qty

    print(f"Stop:   ${stop:.2f}  ({args.stop_pct}% down)")
    print(f"Target: ${target:.2f}  (2:1 = +{args.stop_pct * 2:.1f}%)")
    print(f"Risk:   ${risk_per_share:.2f}/share × {args.qty} = ${total_risk:.2f}  ({total_risk/equity*100:.1f}% of equity)")

    # --- OCO: stop-loss + take-profit (GTC, survives overnight) ---
    oco = client.submit_order(LimitOrderRequest(
        symbol=SYMBOL,
        qty=args.qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC,
        order_class=OrderClass.OCO,
        take_profit=TakeProfitRequest(limit_price=target),
        stop_loss=StopLossRequest(stop_price=stop),
    ))
    print(f"\nOCO order placed:  {oco.id}")
    print(f"\nDone. {args.qty} shares SPCX @ ${fill_price:.2f} | stop ${stop:.2f} | target ${target:.2f}")


if __name__ == "__main__":
    main()
