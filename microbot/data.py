"""
data.py
-------
Fetches historical OHLCV bars from Alpaca's market-data API and returns a clean
pandas DataFrame (columns: open, high, low, close, volume) indexed by timestamp.

Verified against alpaca-py 0.43.4.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pandas as pd
from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout as RequestsTimeout

from alpaca.common.exceptions import APIError
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from .config import settings

# alpaca-py's REST client already retries 429/504 HTTP *responses* automatically
# (3 attempts, 3s apart — see alpaca.common.rest.RESTClient). A raw connection
# timeout never reaches that logic: requests.Session.request() raises before
# there's a response to inspect, so it propagates straight up with zero retry.
# 5xx codes other than 504 (500/502/503) also fall outside alpaca-py's default
# retry_exception_codes=[429, 504]. Confirmed via real incidents in CLAUDE.md —
# "Alpaca paper API reliably congests 9:30-9:50 AM ET" with repeated
# "request timed out" errors during exactly the window research()/engine.py
# scan 40+ symbols. A real 4xx (bad symbol, bad auth) is never retried here —
# retrying won't fix those.
_RETRYABLE_5XX = {500, 502, 503}


def _get_stock_bars_with_retry(client: StockHistoricalDataClient, req: StockBarsRequest,
                               attempts: int = 3, backoff: float = 2.0):
    for attempt in range(attempts):
        try:
            return client.get_stock_bars(req)
        except (RequestsConnectionError, RequestsTimeout):
            if attempt == attempts - 1:
                raise
        except APIError as e:
            if e.status_code not in _RETRYABLE_5XX or attempt == attempts - 1:
                raise
        time.sleep(backoff * (attempt + 1))


_TF = {
    "1Day": TimeFrame.Day,
    "1Hour": TimeFrame.Hour,
    "15Min": TimeFrame(15, TimeFrameUnit.Minute),
    "5Min": TimeFrame(5, TimeFrameUnit.Minute),
    "1Min": TimeFrame.Minute,
}


class MarketData:
    def __init__(self):
        settings.assert_keys()
        self.client = StockHistoricalDataClient(settings.api_key, settings.api_secret)

    def bars(self, symbol: str, timeframe: str | None = None,
             lookback_days: int | None = None) -> pd.DataFrame:
        tf = _TF[timeframe or settings.bar_timeframe]
        days = lookback_days or settings.lookback_days
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=datetime.now() - timedelta(days=days),
        )
        bs = _get_stock_bars_with_retry(self.client, req)
        df = bs.df
        if df.empty:
            return df
        # Multi-index (symbol, timestamp) -> single symbol frame.
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level="symbol")
        return df[["open", "high", "low", "close", "volume"]].copy()
