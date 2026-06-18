"""
Tests for the swing stop ratchet (microbot/trail.py) using fake broker, data
client, and journal — no network, no live orders.

Run: python -m pytest tests/test_trail.py -q
"""
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from unittest.mock import patch

import pytest

from alpaca.trading.enums import OrderSide, OrderType

from microbot.trail import trail_swing_stops


# ---- fakes -----------------------------------------------------------------

@dataclass
class FakeTrade:
    price: float
    size: float = 100.0
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))


class FakeDataClient:
    def __init__(self, trades):
        self.trades = trades  # {symbol: FakeTrade}

    def get_stock_latest_trade(self, req):
        sym = req.symbol_or_symbols
        return {sym: self.trades[sym]}


@dataclass
class FakeOrder:
    id: str
    symbol: str
    side: object = OrderSide.SELL
    type: object = OrderType.STOP
    stop_price: float = 0.0
    order_class: object = None


class FakeClient:
    def __init__(self):
        self.replaced = []  # (order_id, new_stop)
        self.fail_replace = False

    def replace_order_by_id(self, order_id, req):
        if self.fail_replace:
            raise RuntimeError("rejected")
        self.replaced.append((order_id, req.stop_price))


class FakeBroker:
    def __init__(self, positions, orders):
        self._positions = positions
        self._orders = orders
        self.client = FakeClient()

    def positions(self):
        return self._positions

    def open_orders(self):
        return self._orders


def make_world(entry=100.0, stop=95.0, price=110.0, qty=10):
    """One position, one journal row, one stop leg, one fresh trade."""
    positions = [{"symbol": "XYZ", "qty": qty, "avg_entry": entry,
                  "current_price": price, "market_value": qty * price,
                  "unrealized_pl": 0.0, "unrealized_plpc": 0.0}]
    orders = [FakeOrder(id="stop-1", symbol="XYZ", stop_price=stop)]
    journal_rows = [{"symbol": "XYZ", "entry": entry, "stop": stop}]
    broker = FakeBroker(positions, orders)
    data = FakeDataClient({"XYZ": FakeTrade(price=price)})
    return broker, data, journal_rows


def run(broker, data, journal_rows):
    with patch("microbot.trail.journal.fetch_open_orders",
               return_value=journal_rows):
        return trail_swing_stops(broker, data_client=data)


# ---- tests -----------------------------------------------------------------

def test_arms_at_1r_and_trails_to_half_gain():
    # entry 100, stop 95 (risk 5), price 110 (+2R) -> stop to 105
    broker, data, rows = make_world(price=110.0)
    adjusted = run(broker, data, rows)
    assert adjusted == [{"symbol": "XYZ", "old_stop": 95.0,
                         "new_stop": 105.0, "r": 2.0}]
    assert broker.client.replaced == [("stop-1", 105.0)]


def test_below_1r_does_not_arm():
    # +0.8R — must not touch the stop
    broker, data, rows = make_world(price=104.0)
    assert run(broker, data, rows) == []
    assert broker.client.replaced == []


def test_ratchet_never_lowers():
    # Stop already trailed to 106; price implies trail of 105 — no change.
    broker, data, rows = make_world(price=110.0)
    broker._orders[0].stop_price = 106.0
    assert run(broker, data, rows) == []
    assert broker.client.replaced == []


def test_trail_is_always_below_price():
    broker, data, rows = make_world(price=130.0)  # +6R
    adjusted = run(broker, data, rows)
    assert adjusted[0]["new_stop"] == 115.0 < 130.0


def test_ghost_quote_size_zero_skipped():
    broker, data, rows = make_world(price=110.0)
    data.trades["XYZ"].size = 0.0   # SPCX-style reference print
    assert run(broker, data, rows) == []
    assert broker.client.replaced == []


def test_stale_quote_skipped():
    broker, data, rows = make_world(price=110.0)
    data.trades["XYZ"].timestamp = datetime(2020, 1, 2, tzinfo=timezone.utc)
    assert run(broker, data, rows) == []


def test_no_journal_record_skipped():
    broker, data, _ = make_world(price=110.0)
    assert run(broker, data, []) == []
    assert broker.client.replaced == []


def test_no_stop_leg_skipped():
    broker, data, rows = make_world(price=110.0)
    broker._orders = []
    assert run(broker, data, rows) == []


def test_replace_failure_is_contained():
    broker, data, rows = make_world(price=110.0)
    broker.client.fail_replace = True
    assert run(broker, data, rows) == []  # error caught, nothing reported


def test_take_profit_limit_order_not_mistaken_for_stop():
    broker, data, rows = make_world(price=110.0)
    broker._orders = [FakeOrder(id="tp-1", symbol="XYZ",
                                type=OrderType.LIMIT, stop_price=None)]
    assert run(broker, data, rows) == []
    assert broker.client.replaced == []
