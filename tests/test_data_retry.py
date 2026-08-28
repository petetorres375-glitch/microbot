"""
Tests for data.py's retry wrapper around get_stock_bars.

alpaca-py's own REST client already retries 429/504 HTTP *responses*
automatically, but a raw connection timeout never reaches that logic —
requests.Session.request() raises before there's a response to inspect, so
it propagates with zero retry. 5xx codes other than 504 also fall outside
alpaca-py's default retry set. See CLAUDE.md's documented "Alpaca paper API
reliably congests 9:30-9:50 AM ET" incidents for why this matters in
practice — the 9:34/9:35/9:41 AM crons scan 40+ symbols right in that window.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout as RequestsTimeout

from alpaca.common.exceptions import APIError

from microbot.data import _get_stock_bars_with_retry


def _api_error(status_code: int) -> APIError:
    http_error = MagicMock()
    http_error.response.status_code = status_code
    return APIError(json.dumps({"code": 1, "message": "boom"}), http_error)


def test_succeeds_first_try_without_sleeping():
    client = MagicMock()
    client.get_stock_bars.return_value = "bars"
    result = _get_stock_bars_with_retry(client, "req", attempts=3, backoff=0)
    assert result == "bars"
    assert client.get_stock_bars.call_count == 1


def test_retries_on_connection_error_then_succeeds():
    client = MagicMock()
    client.get_stock_bars.side_effect = [RequestsConnectionError("down"), "bars"]
    result = _get_stock_bars_with_retry(client, "req", attempts=3, backoff=0)
    assert result == "bars"
    assert client.get_stock_bars.call_count == 2


def test_retries_on_timeout_then_succeeds():
    client = MagicMock()
    client.get_stock_bars.side_effect = [RequestsTimeout("slow"), "bars"]
    result = _get_stock_bars_with_retry(client, "req", attempts=3, backoff=0)
    assert result == "bars"


def test_retries_on_5xx_api_error_then_succeeds():
    client = MagicMock()
    client.get_stock_bars.side_effect = [_api_error(503), "bars"]
    result = _get_stock_bars_with_retry(client, "req", attempts=3, backoff=0)
    assert result == "bars"


def test_exhausts_attempts_and_raises():
    client = MagicMock()
    client.get_stock_bars.side_effect = RequestsTimeout("still slow")
    with pytest.raises(RequestsTimeout):
        _get_stock_bars_with_retry(client, "req", attempts=3, backoff=0)
    assert client.get_stock_bars.call_count == 3


def test_does_not_retry_4xx_api_error():
    client = MagicMock()
    client.get_stock_bars.side_effect = _api_error(422)
    with pytest.raises(APIError):
        _get_stock_bars_with_retry(client, "req", attempts=3, backoff=0)
    assert client.get_stock_bars.call_count == 1
