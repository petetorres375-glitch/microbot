"""
Tests for timing_gap_check — Part A of the 2026-10-08 backtest-vs-live gap
investigation. All synthetic data, no network.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from microbot import indicators as ind
from microbot import timing_gap_check as tg

NY = ZoneInfo("America/New_York")


def _daily(n=30, end="2026-10-07"):
    # Alpaca-style: midnight ET expressed in UTC.
    idx = pd.bdate_range(end=end, periods=n, tz=NY).tz_convert("UTC")
    return pd.DataFrame({
        "open": np.arange(n, dtype=float) + 10, "high": np.arange(n, dtype=float) + 11,
        "low": np.arange(n, dtype=float) + 9, "close": np.arange(n, dtype=float) + 10.5,
        "volume": np.full(n, 1000.0),
    }, index=idx)


def _intraday(day: dt.date):
    idx = pd.date_range(dt.datetime.combine(day, dt.time(9, 30), tzinfo=NY),
                        periods=4, freq="5min")
    return pd.DataFrame({
        "open": [20.0, 20.5, 21.0, 21.5], "high": [20.8, 21.0, 21.6, 22.0],
        "low": [19.9, 20.4, 20.9, 21.4], "close": [20.5, 21.0, 21.5, 21.9],
        "volume": [500.0, 100.0, 100.0, 100.0],
    }, index=idx)


def test_ny_dates_maps_utc_midnight_to_trading_day():
    d = _daily(3, end="2026-10-07")
    assert tg.ny_dates(d.index)[-1] == dt.date(2026, 10, 7)


def test_partial_bar_takes_only_the_930_bar():
    bar = tg.partial_bar(_intraday(dt.date(2026, 10, 7)))
    assert bar == {"open": 20.0, "high": 20.8, "low": 19.9, "close": 20.5, "volume": 500.0}


def test_partial_bar_missing_returns_none():
    later = _intraday(dt.date(2026, 10, 7)).iloc[1:]
    assert tg.partial_bar(later) is None


def test_live_view_replaces_only_that_day():
    d = _daily(10)
    bar = {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 7.0}
    v = tg.live_view(d, 5, bar)
    assert len(v) == 6                                   # 5 history rows + partial
    assert v.index[-1] == d.index[5]                     # same timestamp as real day
    assert v.iloc[-1]["close"] == 1.5 and v.iloc[-1]["volume"] == 7.0
    pd.testing.assert_frame_equal(v.iloc[:5], d.iloc[:5])
    assert list(v.columns) == list(d.columns)


def test_resolve_target():
    assert tg.resolve(10, 9, 12, [(10.5, 12.5, 10.2, 12.0)]) == (12, "target")


def test_resolve_stop():
    assert tg.resolve(10, 9, 12, [(10.0, 10.5, 8.8, 9.2)]) == (9, "stop")


def test_resolve_gap_through_fills_at_open():
    assert tg.resolve(10, 9, 12, [(8.5, 8.9, 8.0, 8.6)]) == (8.5, "stop")


def test_resolve_ambiguous_bar_counts_as_stop():
    assert tg.resolve(10, 9, 12, [(10.0, 12.5, 8.9, 11.0)]) == (9, "stop")


def test_resolve_open_end_uses_last_close():
    assert tg.resolve(10, 9, 12, [(10, 11, 9.5, 10.4), (10.4, 11.5, 10, 11.2)]) == (11.2, "open_end")


def test_r_multiple():
    assert tg.r_multiple(10, 9, 12) == 2.0
    assert tg.r_multiple(10, 9, 9) == -1.0
    assert tg.r_multiple(10, 10, 12) is None


def test_classify_groups():
    k1, k2, k3 = ("A", "s", 1), ("B", "s", 1), ("C", "s", 1)
    live = {k1: {"id": "L1"}, k2: {"id": "L2"}}
    comp = {k1: {"id": "C1"}, k3: {"id": "C3"}}
    g = tg.classify(live, comp)
    assert g["both"] == [{"live": {"id": "L1"}, "completed": {"id": "C1"}}]
    assert g["live_only"] == [{"id": "L2"}]
    assert g["completed_only"] == [{"id": "C3"}]


def test_simulated_clock_projects_partial_volume_and_restores():
    d = _daily(5, end="2026-10-07")
    when = dt.datetime(2026, 10, 7, 9, 35, tzinfo=NY)
    original = ind.pace_adjusted_volume
    with tg.simulated_clock(when):
        # 5 minutes into a 390-minute session -> x78
        assert ind.pace_adjusted_volume(d) == 1000.0 * 78
    assert ind.pace_adjusted_volume is original


def test_summarize_splits_both_sides():
    rec = lambda r: {"r_multiple": r, "strategy": "s"}
    groups = {"both": [{"live": rec(-1.0), "completed": rec(2.0)}],
              "live_only": [rec(-1.0)], "completed_only": [rec(1.0)]}
    s = tg.summarize(groups)
    assert s["both_as_live"]["expectancy_r"] == -1.0
    assert s["both_as_completed"]["expectancy_r"] == 2.0
    assert s["all_live"]["trades"] == 2 and s["all_live"]["expectancy_r"] == -1.0
    assert s["all_completed"]["expectancy_r"] == 1.5
