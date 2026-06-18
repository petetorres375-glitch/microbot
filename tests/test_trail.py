"""
Tests for the swing stop ratchet (microbot/trail.py) using fake broker, data
client, and journal — no network, no live orders.

Run: python -m pytest tests/test_trail.py -q
"""
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from unittest.mock import patch

import pytest

from alpaca.trading.enums import OrderClass, OrderSide, OrderStatus, OrderType

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
class FakeLeg:
    id: str
    order_type: object
    stop_price: object = None  # float or None
    status: object = OrderStatus.HELD


@dataclass
class FakeOrder:
    id: str
    symbol: str
    side: object = OrderSide.SELL
    type: object = OrderType.STOP
    stop_price: float = 0.0
    order_class: object = None
    legs: list = field(default_factory=list)


class FakeClient:
    def __init__(self):
        self.replaced = []  # (order_id, new_stop)
        self.fail_replace = False
        self.orders_by_id = {}  # id -> FakeOrder, for get_order_by_id

    def replace_order_by_id(self, order_id, req):
        if self.fail_replace:
            raise RuntimeError("rejected")
        self.replaced.append((order_id, req.stop_price))

    def get_order_by_id(self, order_id):
        return self.orders_by_id[order_id]


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


def test_oco_stop_leg_found_via_legs_and_ratchets():
    """OCO target in open_orders → HELD stop found via .legs → ratchets."""
    broker, data, rows = make_world(price=110.0)
    oco_target = FakeOrder(
        id="oco-target-1", symbol="XYZ",
        side=OrderSide.SELL, type=OrderType.LIMIT, stop_price=None,
        order_class=OrderClass.OCO,
        legs=[FakeLeg(id="oco-stop-1", order_type=OrderType.STOP,
                      stop_price=95.0, status=OrderStatus.HELD)],
    )
    broker._orders = [oco_target]
    broker.client.orders_by_id["oco-target-1"] = oco_target
    adjusted = run(broker, data, rows)
    assert adjusted == [{"symbol": "XYZ", "old_stop": 95.0,
                         "new_stop": 105.0, "r": 2.0}]
    assert broker.client.replaced == [("oco-stop-1", 105.0)]


def test_bracket_stop_leg_found_via_journal_parent():
    """No stop in open_orders; bracket parent in journal order_id → HELD stop found → ratchets."""
    broker, data, rows = make_world(price=110.0)
    rows[0]["order_id"] = "bracket-parent-1"
    broker._orders = []  # target leg not visible as a standalone open order
    parent = FakeOrder(
        id="bracket-parent-1", symbol="XYZ",
        side=OrderSide.BUY, type=OrderType.MARKET, stop_price=None,
        order_class=OrderClass.BRACKET,
        legs=[
            FakeLeg(id="bracket-target-1", order_type=OrderType.LIMIT,
                    stop_price=None, status=OrderStatus.NEW),
            FakeLeg(id="bracket-stop-1", order_type=OrderType.STOP,
                    stop_price=95.0, status=OrderStatus.HELD),
        ],
    )
    broker.client.orders_by_id["bracket-parent-1"] = parent
    adjusted = run(broker, data, rows)
    assert adjusted == [{"symbol": "XYZ", "old_stop": 95.0,
                         "new_stop": 105.0, "r": 2.0}]
    assert broker.client.replaced == [("bracket-stop-1", 105.0)]


def test_oco_canceled_stop_leg_not_used():
    """OCO target whose stop leg is CANCELED → no stop found → no ratchet."""
    broker, data, rows = make_world(price=110.0)
    oco_target = FakeOrder(
        id="oco-target-2", symbol="XYZ",
        side=OrderSide.SELL, type=OrderType.LIMIT, stop_price=None,
        order_class=OrderClass.OCO,
        legs=[FakeLeg(id="oco-stop-2", order_type=OrderType.STOP,
                      stop_price=95.0, status=OrderStatus.CANCELED)],
    )
    broker._orders = [oco_target]
    broker.client.orders_by_id["oco-target-2"] = oco_target
    assert run(broker, data, rows) == []
    assert broker.client.replaced == []
