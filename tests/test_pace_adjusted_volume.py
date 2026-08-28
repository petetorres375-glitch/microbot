"""
Tests for indicators.pace_adjusted_volume — see the 2026-08-28 breakout
drought investigation: Breakout/Breakout52w compare the latest bar's volume
against a many-day average, but the swing engine runs at 9:34 AM ET, ~4
minutes into the session. Alpaca's "1Day" bar for the current trading day
updates live and only reflects trades since the open, so the raw comparison
silently failed almost every time the engine ran regardless of the eventual
full-day volume — confirmed via backtest replay finding real breakout
entries on days the live engine never surfaced a signal for.
"""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from microbot.indicators import pace_adjusted_volume

ET = ZoneInfo("America/New_York")


def _df_with_last_bar(date: str, volume: float) -> pd.DataFrame:
    dates = pd.bdate_range(end=date, periods=30)
    return pd.DataFrame({
        "open": np.full(30, 10.0), "high": np.full(30, 10.5),
        "low": np.full(30, 9.5), "close": np.full(30, 10.0),
        "volume": np.full(30, 500_000.0),
    }, index=dates).assign(volume=lambda d: d["volume"].mask(
        d.index == d.index[-1], volume))


def test_completed_historical_bar_returned_unchanged():
    df = _df_with_last_bar("2026-08-18", volume=1_000_000)
    now = datetime.datetime(2026, 8, 28, 10, 0, tzinfo=ET)  # ten days later
    assert pace_adjusted_volume(df, now=now) == 1_000_000


def test_todays_partial_bar_is_projected_up():
    df = _df_with_last_bar("2026-08-28", volume=50_000)
    # 9:34 AM ET — 4 minutes into the 390-minute session, matching the
    # real 9:34 AM engine cron.
    now = datetime.datetime(2026, 8, 28, 9, 34, tzinfo=ET)
    result = pace_adjusted_volume(df, now=now)
    assert result == 50_000 * (390 / 4)
    assert result > 4_000_000  # would clear a 1.2x-of-20-day-avg gate easily


def test_todays_bar_after_close_returned_unchanged():
    df = _df_with_last_bar("2026-08-28", volume=900_000)
    now = datetime.datetime(2026, 8, 28, 16, 30, tzinfo=ET)  # after 4pm close
    assert pace_adjusted_volume(df, now=now) == 900_000


def test_defaults_to_real_clock_without_crashing():
    df = _df_with_last_bar("2026-08-18", volume=1_000_000)
    # No `now` passed — must not raise, regardless of when the suite runs.
    assert pace_adjusted_volume(df) >= 0
