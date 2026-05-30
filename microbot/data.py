"""
data.py
-------
Fetches historical OHLCV bars from Alpaca's market-data API and returns a clean
pandas DataFrame (columns: open, high, low, close, volume) indexed by timestamp.

Verified against alpaca-py 0.43.4.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from .config import settings

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
        bs = self.client.get_stock_bars(req)
        df = bs.df
        if df.empty:
            return df
        # Multi-index (symbol, timestamp) -> single symbol frame.
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level="symbol")
        return df[["open", "high", "low", "close", "volume"]].copy()
