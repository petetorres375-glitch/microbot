"""
broker.py
---------
Thin wrapper around alpaca-py's TradingClient. Handles the paper<->live switch,
account info, current positions, and submitting BRACKET orders (entry + stop +
2:1 take-profit in a single order, so the risk leg can never be "forgotten").

Verified against alpaca-py 0.43.4.
"""
from __future__ import annotations

from typing import List

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest, LimitOrderRequest,
    TakeProfitRequest, StopLossRequest, GetOrdersRequest,
)
from alpaca.trading.enums import OrderSide, OrderClass, TimeInForce, QueryOrderStatus

from .config import settings
from .risk import SizedTrade


class Broker:
    def __init__(self, paper: bool | None = None):
        settings.assert_keys()
        self.paper = (not settings.live_trading) if paper is None else paper
        self.client = TradingClient(settings.api_key, settings.api_secret,
                                    paper=self.paper)

    # ---- account / state ----
    def account(self):
        a = self.client.get_account()
        return {
            "equity": float(a.equity),
            "cash": float(a.cash),
            "buying_power": float(a.buying_power),
            "daytrade_count": int(a.daytrade_count),
            "pattern_day_trader": bool(a.pattern_day_trader),
            "currency": a.currency,
        }

    def positions(self) -> List[dict]:
        out = []
        for p in self.client.get_all_positions():
            out.append({
                "symbol": p.symbol, "qty": float(p.qty),
                "avg_entry": float(p.avg_entry_price),
                "current_price": float(p.current_price or 0),
                "market_value": float(p.market_value or 0),
                "unrealized_pl": float(p.unrealized_pl or 0),
                "unrealized_plpc": float(p.unrealized_plpc or 0),
            })
        return out

    def open_orders(self):
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        return self.client.get_orders(filter=req)

    def held_symbols(self):
        return {p["symbol"] for p in self.positions()}

    # ---- order placement ----
    def submit_bracket(self, trade: SizedTrade):
        """
        Submit a long bracket: market entry, with a take-profit leg at the 2:1
        target and a stop-loss leg at the protective stop. Whole shares only.
        """
        s = trade.signal
        order = MarketOrderRequest(
            symbol=s.symbol,
            qty=trade.qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(s.target, 2)),
            stop_loss=StopLossRequest(stop_price=round(s.stop, 2)),
        )
        return self.client.submit_order(order)

    def close_all(self):
        """Emergency flatten — cancels orders and closes every position."""
        return self.client.close_all_positions(cancel_orders=True)
