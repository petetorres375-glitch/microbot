"""Tests for recent_edge_gate_test (step 3b of the 2026-10-08 investigation)."""
from __future__ import annotations

import datetime as dt

from microbot import recent_edge_gate_test as g

D = dt.date


def _t(entry, exit_, r, strat="s"):
    return {"strategy": strat, "entry_date": entry, "exit_date": exit_, "r_multiple": r}


def test_gate_off_when_recent_closed_trades_negative():
    hist = [_t(D(2026, 1, i), D(2026, 1, i + 1), -1.0) for i in range(1, 11)]
    on, n, avg = g.gate_state(hist, D(2026, 2, 1), 91, min_trades=10)
    assert (on, n, avg) == (False, 10, -1.0)


def test_gate_on_when_positive():
    hist = [_t(D(2026, 1, i), D(2026, 1, i + 1), 1.0) for i in range(1, 11)]
    assert g.gate_state(hist, D(2026, 2, 1), 91, min_trades=10)[0] is True


def test_too_few_trades_leaves_gate_on():
    hist = [_t(D(2026, 1, 1), D(2026, 1, 2), -1.0)]
    assert g.gate_state(hist, D(2026, 2, 1), 91, min_trades=10) == (True, 1, None)


def test_no_lookahead_ignores_trades_not_yet_closed():
    # Losers that close ON or AFTER the decision date must not count.
    hist = [_t(D(2026, 1, 1), D(2026, 2, 1), -1.0) for _ in range(10)]
    assert g.gate_state(hist, D(2026, 2, 1), 91, min_trades=10) == (True, 0, None)


def test_window_excludes_old_entries():
    hist = [_t(D(2025, 1, 1), D(2025, 1, 2), -1.0) for _ in range(10)]
    assert g.gate_state(hist, D(2026, 2, 1), 91, min_trades=10)[1] == 0


def test_label_trades_warmup_and_per_strategy_isolation():
    # Strategy "bad" loses steadily; "good" wins. Gate should block later
    # "bad" trades but never "good" ones (strategies are judged separately).
    trades = []
    for i in range(40):
        day = D(2026, 1, 1) + dt.timedelta(days=i * 3)
        trades.append(_t(day, day + dt.timedelta(days=1), -1.0, "bad"))
        trades.append(_t(day, day + dt.timedelta(days=1), 1.0, "good"))
    lab = g.label_trades(trades, window_days=30, first_date=D(2026, 1, 1), min_trades=5)
    assert all(t["entry_date"] < D(2026, 1, 31) for t in lab["warmup"])
    assert all(t["strategy"] == "bad" for t in lab["off"])
    assert all(t["strategy"] == "good" for t in lab["on"])
    assert lab["off"]


def test_compare_shapes():
    lab = {"on": [_t(D(2026, 1, 1), D(2026, 1, 2), 1.0)],
           "off": [_t(D(2026, 1, 1), D(2026, 1, 2), -1.0)], "warmup": []}
    c = g.compare(lab)
    assert c["all"]["trades"] == 2 and c["all"]["expectancy_r"] == 0.0
    assert c["on"]["expectancy_r"] == 1.0 and c["off"]["expectancy_r"] == -1.0
