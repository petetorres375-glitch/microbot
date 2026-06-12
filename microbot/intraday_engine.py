"""
intraday_engine.py
------------------
Opening Range Breakout (ORB) day trading engine.

Workflow:
  1. Load pre-market candidates from intraday_candidates.json
  2. At 9:35 AM ET: fetch the 9:30-9:35 candle to establish the opening range
  3. Enter long when price breaks above ORB high (within first 30 min)
  4. Stop = ORB low; Target = entry + 2 * (entry - ORB low)
  5. At 2:1 target: sell half, move stop to breakeven
  6. Trail remaining: stop trails at 50% of max gain above entry
  7. Hard close all positions at 3:55 PM ET

Risk rules:
  - 1% equity risk per trade
  - Max 2 concurrent intraday positions
  - 2% daily loss limit halts trading for the rest of the day
  - All positions closed EOD — never held overnight
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, List, Optional
import zoneinfo

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockSnapshotRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    GetOrdersRequest, MarketOrderRequest, StopOrderRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce

from .config import settings
from . import journal

ET = zoneinfo.ZoneInfo("America/New_York")

MAX_CONCURRENT = 2
DAILY_LOSS_LIMIT_PCT = 0.02
RISK_PER_TRADE_PCT = 0.01
POLL_INTERVAL = 30          # seconds between price checks
ORB_WINDOW_MINUTES = 5      # length of opening range
ENTRY_CUTOFF = (10, 0)      # no new entries after 10:00 AM ET
EOD_CLOSE = (15, 55)        # hard close at 3:55 PM ET
CANDIDATES_FILE = "intraday_candidates.json"


@dataclass
class ORBState:
    symbol: str
    orb_high: float = 0.0
    orb_low: float = 0.0
    orb_established: bool = False

    in_trade: bool = False
    qty_total: int = 0
    qty_remaining: int = 0
    entry_price: float = 0.0
    stop_price: float = 0.0
    initial_risk: float = 0.0   # entry - original stop; stop_price mutates as we trail
    target_price: float = 0.0
    half_exited: bool = False
    highest_price: float = 0.0
    stop_order_id: str = ""

    closed: bool = False
    realized_pnl: float = 0.0


class IntradayEngine:
    def __init__(self):
        settings.assert_keys()
        self.trading = TradingClient(
            settings.api_key, settings.api_secret,
            paper=not settings.live_trading,
        )
        self.data = StockHistoricalDataClient(settings.api_key, settings.api_secret)
        # Paper account shows Alpaca's $100k default — size risk off the
        # simulated stake instead, same as engine.py does for swing trades.
        if settings.live_trading:
            self.equity = float(self.trading.get_account().equity)
        else:
            self.equity = settings.starting_equity
        self.states: Dict[str, ORBState] = {}
        journal.init()
        self._init_daily()

    # ---- journal helpers ----

    def _init_daily(self):
        today = date.today().isoformat()
        with journal._conn() as con:
            con.execute(
                "INSERT OR IGNORE INTO intraday_daily(date, realized_pnl, trades_taken, halted)"
                " VALUES(?, 0, 0, 0)",
                (today,),
            )

    def _daily_pnl(self) -> float:
        today = date.today().isoformat()
        with journal._conn() as con:
            row = con.execute(
                "SELECT realized_pnl FROM intraday_daily WHERE date=?", (today,)
            ).fetchone()
        return float(row["realized_pnl"]) if row else 0.0

    def _record_pnl(self, pnl: float):
        today = date.today().isoformat()
        with journal._conn() as con:
            con.execute(
                "UPDATE intraday_daily SET realized_pnl = realized_pnl + ?,"
                " trades_taken = trades_taken + 1 WHERE date=?",
                (pnl, today),
            )

    def _halt(self):
        today = date.today().isoformat()
        with journal._conn() as con:
            con.execute("UPDATE intraday_daily SET halted=1 WHERE date=?", (today,))

    def _is_halted(self) -> bool:
        today = date.today().isoformat()
        with journal._conn() as con:
            row = con.execute(
                "SELECT halted FROM intraday_daily WHERE date=?", (today,)
            ).fetchone()
        return bool(row["halted"]) if row else False

    def _log_open(self, sym: str, s: ORBState):
        with journal._conn() as con:
            con.execute(
                "INSERT INTO intraday_trades"
                "(date,symbol,strategy,qty,entry,stop,target,status,ts_open)"
                " VALUES(?,?,?,?,?,?,?,'open',?)",
                (date.today().isoformat(), sym, "orb", s.qty_total,
                 s.entry_price, s.stop_price, s.target_price,
                 datetime.now(timezone.utc).isoformat()),
            )

    def _log_closed(self, sym: str, exit_price: float, reason: str,
                    pnl: float, r_multiple: float,
                    half_exit_price: Optional[float] = None):
        today = date.today().isoformat()
        with journal._conn() as con:
            con.execute(
                "UPDATE intraday_trades SET exit_price=?, exit_reason=?,"
                " half_exit_price=?, pnl=?, r_multiple=?, status='closed', ts_close=?"
                " WHERE date=? AND symbol=? AND status='open'",
                (round(exit_price, 2), reason, half_exit_price,
                 round(pnl, 2), round(r_multiple, 2),
                 datetime.now(timezone.utc).isoformat(), today, sym),
            )

    # ---- time helpers ----

    def _now_et(self) -> datetime:
        return datetime.now(ET)

    def _past(self, hour: int, minute: int) -> bool:
        now = self._now_et()
        return now.hour > hour or (now.hour == hour and now.minute >= minute)

    def _market_open(self) -> bool:
        now = self._now_et()
        if now.weekday() >= 5:
            return False
        return self._past(9, 30) and not self._past(16, 0)

    # ---- data helpers ----

    def _establish_orb(self):
        pending = [sym for sym, s in self.states.items()
                   if not s.orb_established and not s.closed]
        if not pending:
            return

        today = date.today()
        open_dt = datetime(today.year, today.month, today.day, 9, 30, tzinfo=ET)
        end = datetime(today.year, today.month, today.day, 9, 41, tzinfo=ET)
        open_ts = pd.Timestamp(open_dt)

        req = StockBarsRequest(
            symbol_or_symbols=pending,
            timeframe=TimeFrame(ORB_WINDOW_MINUTES, TimeFrameUnit.Minute),
            start=open_dt,
            end=end,
            feed=DataFeed.IEX,
        )
        try:
            bars_df = self.data.get_stock_bars(req).df
        except Exception as e:
            print(f"ORB fetch error: {e}")
            return

        if bars_df.empty:
            return

        def first_orb_bar(df):
            # Only the bar stamped exactly 9:30 is the opening range — a thin
            # stock with no 9:30 IEX trades returns 9:35+ bars first, and
            # those would set a false range.
            match = df[df.index == open_ts]
            return match.iloc[0] if not match.empty else None

        if isinstance(bars_df.index, pd.MultiIndex):
            for sym in pending:
                try:
                    sym_bars = bars_df.xs(sym, level="symbol")
                except KeyError:
                    continue
                first = first_orb_bar(sym_bars)
                if first is None:
                    continue
                self._set_orb(sym, float(first["high"]), float(first["low"]))
        else:
            # Single symbol
            first = first_orb_bar(bars_df)
            if first is not None:
                self._set_orb(pending[0], float(first["high"]), float(first["low"]))

    def _set_orb(self, sym: str, high: float, low: float):
        s = self.states[sym]
        s.orb_high = high
        s.orb_low = low
        s.orb_established = True
        print(f"  ORB {sym}: high={high:.2f}  low={low:.2f}  "
              f"range={high - low:.2f}")

    def _get_prices(self) -> Dict[str, float]:
        symbols = list(self.states.keys())
        try:
            # Default SIP feed — snapshots are allowed on the free plan;
            # only recent historical bars require IEX.
            snaps = self.data.get_stock_snapshot(
                StockSnapshotRequest(symbol_or_symbols=symbols)
            )
            return {sym: float(snap.latest_trade.price)
                    for sym, snap in snaps.items()
                    if snap.latest_trade}
        except Exception:
            return {}

    # ---- order helpers ----

    def _cancel_stop(self, s: ORBState):
        if not s.stop_order_id:
            return
        order_id = s.stop_order_id
        s.stop_order_id = ""   # clear before cancel so fill-detection ignores it
        try:
            self.trading.cancel_order_by_id(order_id)
        except Exception:
            pass
        # Shares stay held until the cancel is processed — a replacement stop
        # submitted too early is rejected, leaving the position unprotected.
        for _ in range(10):
            try:
                status = str(self.trading.get_order_by_id(order_id).status).lower()
            except Exception:
                return
            if any(t in status for t in ("canceled", "filled", "expired", "rejected")):
                return
            time.sleep(1)

    def _submit_stop(self, sym: str, qty: int, stop_price: float,
                     attempts: int = 3) -> str:
        for i in range(attempts):
            try:
                order = self.trading.submit_order(StopOrderRequest(
                    symbol=sym,
                    qty=qty,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                    stop_price=round(stop_price, 2),
                ))
                return str(order.id)
            except Exception as e:
                print(f"  stop order failed {sym} "
                      f"(attempt {i + 1}/{attempts}): {e}")
                time.sleep(2)
        return ""

    # ---- trade lifecycle ----

    def _enter(self, sym: str, price: float) -> bool:
        s = self.states[sym]
        risk_per_share = price - s.orb_low
        if risk_per_share <= 0:
            return False

        risk_budget = self.equity * RISK_PER_TRADE_PCT
        qty = int(risk_budget // risk_per_share)
        if qty < 1:
            print(f"  skip {sym}: 1 share risks ${risk_per_share:.2f} > "
                  f"${risk_budget:.2f} budget")
            return False

        # Market entry
        try:
            entry_order = self.trading.submit_order(MarketOrderRequest(
                symbol=sym,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            ))
        except Exception as e:
            print(f"  entry failed {sym}: {e}")
            return False

        time.sleep(2)  # let fill propagate
        try:
            filled = self.trading.get_order_by_id(entry_order.id)
            fill = float(filled.filled_avg_price or price)
        except Exception:
            fill = price

        stop = s.orb_low
        target = round(fill + 2.0 * (fill - stop), 2)

        # Submit protective stop immediately
        stop_id = self._submit_stop(sym, qty, stop)
        if not stop_id:
            # Never hold an unprotected position — bail out at market
            print(f"  ABORT {sym}: stop could not be placed, closing entry")
            try:
                self.trading.submit_order(MarketOrderRequest(
                    symbol=sym, qty=qty,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                ))
            except Exception as e:
                print(f"  EMERGENCY: close also failed {sym}: {e} — "
                      f"manual intervention needed")
            s.closed = True
            return False

        s.in_trade = True
        s.qty_total = qty
        s.qty_remaining = qty
        s.entry_price = fill
        s.stop_price = stop
        s.initial_risk = fill - stop
        s.target_price = target
        s.highest_price = fill
        s.stop_order_id = stop_id

        dollar_risk = round(qty * (fill - stop), 2)
        print(f"  ENTER {sym}: {qty} shares @ {fill:.2f}  "
              f"stop={stop:.2f}  target={target:.2f}  risk=${dollar_risk:.2f}")
        self._log_open(sym, s)
        return True

    def _manage(self, sym: str, price: float):
        s = self.states[sym]
        if not s.in_trade or s.closed:
            return

        if price > s.highest_price:
            s.highest_price = price

        # Scale out at 2:1
        if not s.half_exited and price >= s.target_price:
            half = s.qty_total // 2
            if half < 1:
                # Too small to scale — take the full target
                self._close_position(sym, price, "target")
                return
            if half >= 1:
                self._cancel_stop(s)
                try:
                    self.trading.submit_order(MarketOrderRequest(
                        symbol=sym, qty=half,
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.DAY,
                    ))
                    s.half_exited = True
                    s.realized_pnl += half * (price - s.entry_price)
                    s.qty_remaining = s.qty_total - half
                    print(f"  SCALE OUT {sym}: sold {half} @ {price:.2f}  "
                          f"(2:1 = {s.target_price:.2f})")

                    if s.qty_remaining >= 1:
                        # Move stop to breakeven — but never below a level the
                        # pre-scale-out trail already reached
                        new_stop = max(s.entry_price, s.stop_price)
                        s.stop_order_id = self._submit_stop(
                            sym, s.qty_remaining, new_stop
                        )
                        if not s.stop_order_id:
                            self._close_position(sym, price, "stop_failed")
                            return
                        s.stop_price = new_stop
                        print(f"  STOP → breakeven {sym}: {new_stop:.2f}")
                    else:
                        self._finalize(sym, price, "target")
                except Exception as e:
                    print(f"  scale-out failed {sym}: {e}")
            return

        # Trail: stop = entry + 50% of max gain. Arms after the scale-out, OR
        # as soon as the position is up 1R — so a runner that stalls just
        # under the 2:1 target (UBXG 2026-06-12: +1.7R high, never scaled)
        # gives back half its max gain at worst, not the full original risk.
        gain = s.highest_price - s.entry_price
        armed = s.half_exited or (s.initial_risk > 0 and gain >= s.initial_risk)
        if armed and s.qty_remaining >= 1:
            if gain > 0:
                trail = s.entry_price + 0.5 * gain
                if trail > s.stop_price + 0.05:
                    self._cancel_stop(s)
                    s.stop_order_id = self._submit_stop(
                        sym, s.qty_remaining, trail
                    )
                    if not s.stop_order_id:
                        self._close_position(sym, price, "stop_failed")
                        return
                    s.stop_price = round(trail, 2)
                    print(f"  TRAIL {sym}: stop → {s.stop_price:.2f}")

    def _check_stop_fills(self):
        """Detect when Alpaca filled a stop order and record the closed trade."""
        if not any(s.in_trade for s in self.states.values()):
            return

        try:
            open_ids = {
                str(o.id)
                for o in self.trading.get_orders(
                    filter=GetOrdersRequest(status="open")
                )
            }
        except Exception:
            return

        for sym, s in self.states.items():
            if not s.in_trade or s.closed or not s.stop_order_id:
                continue
            if s.stop_order_id not in open_ids:
                # Stop was filled (we only clear stop_order_id before intentional cancels)
                exit_price = s.stop_price
                s.realized_pnl += s.qty_remaining * (exit_price - s.entry_price)
                self._finalize(sym, exit_price, "stop")

    def _finalize(self, sym: str, exit_price: float, reason: str):
        s = self.states[sym]
        s.closed = True
        s.in_trade = False
        total_pnl = round(s.realized_pnl, 2)
        risk = s.qty_total * (s.entry_price - s.stop_price)
        r = round(total_pnl / risk, 2) if risk > 0 else 0.0
        print(f"  CLOSED {sym}: {reason}  exit={exit_price:.2f}  "
              f"pnl=${total_pnl:.2f}  {r:+.1f}R")
        self._record_pnl(total_pnl)
        half_price = (s.target_price if s.half_exited else None)
        self._log_closed(sym, exit_price, reason, total_pnl, r, half_price)

    def _close_position(self, sym: str, price: float, reason: str):
        s = self.states[sym]
        self._cancel_stop(s)
        if s.qty_remaining >= 1:
            try:
                self.trading.submit_order(MarketOrderRequest(
                    symbol=sym, qty=s.qty_remaining,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                ))
                s.realized_pnl += s.qty_remaining * (price - s.entry_price)
            except Exception as e:
                print(f"  close failed {sym}: {e}")
        self._finalize(sym, price, reason)

    def _eod_close_all(self, prices: Dict[str, float]):
        print("3:55 PM — closing all intraday positions")
        for sym, s in self.states.items():
            if s.in_trade and not s.closed:
                self._close_position(sym, prices.get(sym, s.entry_price), "eod_close")

    # ---- main loop ----

    def run(self):
        candidates = self._load_candidates()
        if not candidates:
            return

        for sym in candidates:
            self.states[sym] = ORBState(symbol=sym)

        print(f"\nWatching: {', '.join(candidates)}")
        print(f"Account equity: ${self.equity:,.2f}  "
              f"Risk/trade: ${self.equity * RISK_PER_TRADE_PCT:.2f}  "
              f"Daily loss limit: ${self.equity * DAILY_LOSS_LIMIT_PCT:.2f}")

        orb_logged = False
        while True:
            now = self._now_et()

            if not self._market_open():
                if not self._past(9, 30):
                    print(f"  waiting for market open... ({now.strftime('%H:%M')} ET)")
                time.sleep(15)
                continue

            if self._past(*EOD_CLOSE):
                prices = self._get_prices()
                self._eod_close_all(prices)
                break

            # Establish ORB after first 5-min candle closes (9:35).
            # The 9:30 bar may not be published at exactly 9:35:00, so keep
            # retrying every loop until all ranges are set (or entries are
            # cut off and it no longer matters).
            if (self._past(9, 35)
                    and not self._past(*ENTRY_CUTOFF)
                    and any(not s.orb_established and not s.closed
                            for s in self.states.values())):
                if not orb_logged:
                    print(f"\n--- Establishing opening ranges ({now.strftime('%H:%M')} ET) ---")
                    orb_logged = True
                self._establish_orb()

            prices = self._get_prices()

            # Detect stop fills
            self._check_stop_fills()

            daily_pnl = self._daily_pnl()
            loss_limit = -(self.equity * DAILY_LOSS_LIMIT_PCT)

            if daily_pnl <= loss_limit and not self._is_halted():
                print(f"\nDaily loss limit hit (${daily_pnl:.2f}). Halting.")
                self._halt()
                self._eod_close_all(prices)
                break

            active = sum(1 for s in self.states.values()
                         if s.in_trade and not s.closed)

            for sym, s in self.states.items():
                price = prices.get(sym)
                if price is None or s.closed:
                    continue

                if s.in_trade:
                    self._manage(sym, price)
                elif (s.orb_established
                      and not self._is_halted()
                      and active < MAX_CONCURRENT
                      and not self._past(*ENTRY_CUTOFF)
                      and price > s.orb_high):
                    print(f"\n  BREAKOUT {sym}: {price:.2f} > ORB {s.orb_high:.2f}")
                    if self._enter(sym, price):
                        active += 1

            time.sleep(POLL_INTERVAL)

        total = self._daily_pnl()
        print(f"\nSession complete. Day P&L: ${total:+.2f}")

    def _load_candidates(self) -> List[str]:
        try:
            with open(CANDIDATES_FILE) as f:
                data = json.load(f)
            if data.get("date") == date.today().isoformat():
                syms = [c["symbol"] for c in data.get("candidates", [])]
                print(f"Loaded {len(syms)} candidates from {CANDIDATES_FILE}: "
                      f"{', '.join(syms)}")
                return syms
        except FileNotFoundError:
            print(f"No {CANDIDATES_FILE} found. Run the scanner first:\n"
                  f"  python -m microbot.intraday_scanner")
        except Exception as e:
            print(f"Could not load candidates: {e}")
        return []
