"""
Tests for broker.py's order-failure diagnostics and idempotency key.

See the 2026-08-28 Alpaca-skill comparison: engine.py's order submission
caught a bare Exception and printed the raw message, losing the fact that
Alpaca's POST /v2/orders only ever returns two meaningful error bodies whose
HTTP names are misleading (403 = insufficient buying power/shares, not auth;
422 = bad params or an untradable symbol, not "not found"). submit_bracket()
also had no client_order_id, so a retry after a timed-out submission (Alpaca's
paper API congests 9:30-9:50 AM ET) had no way to detect an accidental
duplicate.
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

from alpaca.common.exceptions import APIError

from microbot.broker import Broker, describe_order_error
from microbot.risk import SizedTrade
from microbot.strategies import Signal


def _api_error(status_code: int, message: str) -> APIError:
    http_error = MagicMock()
    http_error.response.status_code = status_code
    return APIError(json.dumps({"code": status_code * 100, "message": message}), http_error)


def test_describe_403_is_buying_power_not_auth():
    e = _api_error(403, "insufficient buying power")
    msg = describe_order_error(e)
    assert "buying power" in msg
    assert "insufficient buying power" in msg  # original detail preserved


def test_describe_422_is_bad_params_not_not_found():
    e = _api_error(422, "invalid symbol")
    msg = describe_order_error(e)
    assert "untradable symbol" in msg or "invalid order parameters" in msg


def test_describe_429_says_back_off():
    e = _api_error(429, "rate limited")
    assert "back off" in describe_order_error(e)


def test_describe_401_says_do_not_retry():
    e = _api_error(401, "bad key")
    assert "do not retry" in describe_order_error(e)


def test_describe_unknown_code_falls_back_to_detail():
    e = _api_error(500, "server error")
    assert describe_order_error(e) == "server error"


def test_describe_non_api_error_returns_str():
    assert describe_order_error(ValueError("boom")) == "boom"


def test_submit_bracket_sets_deterministic_client_order_id():
    broker = Broker.__new__(Broker)
    broker.client = MagicMock()
    sig = Signal("AAPL", "trend_momentum", "buy", 100.0, 95.0, 110.0, 2.0, "test")
    trade = SizedTrade(signal=sig, qty=5, dollar_risk=25.0, notional=500.0)

    broker.submit_bracket(trade)

    submitted = broker.client.submit_order.call_args[0][0]
    assert submitted.client_order_id == f"trend_momentum-AAPL-{date.today().isoformat()}"
