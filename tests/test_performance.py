"""
Tests for microbot/performance.py — no network, no live DB.

Run: python -m pytest tests/test_performance.py -q
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from microbot.performance import (
    _load_intraday_trades,
    _load_swing_trades,
    compute_metrics,
    print_report,
)

# ---- minimal schema for test DBs -------------------------------------------

_SCHEMA = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    ts TEXT, symbol TEXT, strategy TEXT, qty INTEGER,
    entry REAL, exit REAL, outcome TEXT, pnl REAL, r_multiple REAL
);
CREATE TABLE intraday_trades (
    id INTEGER PRIMARY KEY,
    date TEXT, symbol TEXT, strategy TEXT DEFAULT 'orb',
    qty INTEGER, entry REAL, stop REAL, target REAL,
    half_exit_price REAL, exit_price REAL, exit_reason TEXT,
    pnl REAL, r_multiple REAL,
    status TEXT DEFAULT 'open',
    alpaca_entry_id TEXT, alpaca_stop_id TEXT,
    ts_open TEXT, ts_close TEXT
);
"""


def _make_db(swing_rows=None, intraday_rows=None) -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    if swing_rows:
        conn.executemany(
            "INSERT INTO trades(ts,symbol,strategy,qty,entry,exit,outcome,pnl,r_multiple)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            swing_rows,
        )
    if intraday_rows:
        conn.executemany(
            "INSERT INTO intraday_trades(date,symbol,strategy,qty,entry,stop,target,"
            "exit_price,exit_reason,pnl,r_multiple,status,ts_open,ts_close)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            intraday_rows,
        )
    conn.commit()
    conn.close()
    return path


# ---- compute_metrics --------------------------------------------------------

def test_metrics_empty():
    assert compute_metrics([])["n"] == 0


def test_metrics_all_wins():
    trades = [
        {"pnl": 100.0, "r_multiple": 2.0, "symbol": "A"},
        {"pnl": 50.0, "r_multiple": 1.0, "symbol": "B"},
    ]
    m = compute_metrics(trades)
    assert m["n"] == 2
    assert m["wins"] == 2
    assert m["losses"] == 0
    assert m["win_rate"] == 100.0
    assert m["total_pnl"] == 150.0
    assert m["expectancy_r"] == 1.5
    assert m["avg_win_r"] == 1.5
    assert m["avg_loss_r"] == 0.0
    assert m["max_consec_loss"] == 0
    assert m["best_sym"] == "A"
    assert m["worst_sym"] == "B"


def test_metrics_mixed():
    trades = [
        {"pnl": 88.76, "r_multiple": 1.524, "symbol": "TGTX"},
        {"pnl": -52.36, "r_multiple": -1.0, "symbol": "KEEL"},
        {"pnl": 105.98, "r_multiple": 2.01, "symbol": "SPCX"},
        {"pnl": -44.07, "r_multiple": -1.008, "symbol": "GOOG"},
    ]
    m = compute_metrics(trades)
    assert m["n"] == 4
    assert m["wins"] == 2
    assert m["losses"] == 2
    assert m["win_rate"] == 50.0
    assert abs(m["total_pnl"] - 98.31) < 0.01
    assert m["best_sym"] == "SPCX"
    assert m["worst_sym"] == "KEEL"
    assert m["max_consec_loss"] == 1


def test_metrics_consecutive_losses():
    trades = [
        {"pnl": -10.0, "r_multiple": -1.0, "symbol": "A"},
        {"pnl": -10.0, "r_multiple": -1.0, "symbol": "B"},
        {"pnl": -10.0, "r_multiple": -1.0, "symbol": "C"},
        {"pnl": 30.0, "r_multiple": 1.0, "symbol": "D"},
    ]
    m = compute_metrics(trades)
    assert m["max_consec_loss"] == 3


def test_metrics_single_trade():
    trades = [{"pnl": -30.0, "r_multiple": -1.0, "symbol": "X"}]
    m = compute_metrics(trades)
    assert m["n"] == 1
    assert m["win_rate"] == 0.0
    assert m["avg_loss_r"] == -1.0
    assert m["avg_win_r"] == 0.0
    assert m["max_consec_loss"] == 1


# ---- _load_swing_trades filtering ------------------------------------------

def test_load_swing_excludes_no_fill():
    path = _make_db(swing_rows=[
        ("2026-06-01", "XYZ", "trend_momentum", 10, 100.0, 100.0, "no_fill", 0.0, 0.0),
        ("2026-06-02", "ABC", "breakout", 5, 50.0, 55.0, "target", 25.0, 1.0),
    ])
    trades = _load_swing_trades(path)
    os.unlink(path)
    assert len(trades) == 1
    assert trades[0]["symbol"] == "ABC"


def test_load_swing_excludes_zero_pnl_manual():
    path = _make_db(swing_rows=[
        ("2026-06-01", "XYZ", "breakout", 10, 100.0, 100.0, "manual", 0.0, 0.0),
        ("2026-06-02", "ABC", "breakout", 5, 50.0, 48.0, "manual", -10.0, -0.5),
    ])
    trades = _load_swing_trades(path)
    os.unlink(path)
    assert len(trades) == 1
    assert trades[0]["symbol"] == "ABC"
    assert trades[0]["pnl"] == -10.0


def test_load_swing_includes_nonzero_pnl_manual():
    path = _make_db(swing_rows=[
        ("2026-06-01", "BTI", "dividend_momentum", 17, 62.0, 59.0, "manual", -51.0, -0.6),
    ])
    trades = _load_swing_trades(path)
    os.unlink(path)
    assert len(trades) == 1


def test_load_swing_includes_stop_target_loss():
    path = _make_db(swing_rows=[
        ("2026-06-01", "A", "trend_momentum", 5, 100.0, 90.0, "stop", -50.0, -1.0),
        ("2026-06-02", "B", "breakout", 5, 50.0, 60.0, "target", 50.0, 2.0),
        ("2026-06-03", "C", "mean_reversion", 5, 40.0, 35.0, "loss", -25.0, -1.0),
    ])
    trades = _load_swing_trades(path)
    os.unlink(path)
    assert len(trades) == 3


def test_load_swing_empty_db():
    path = _make_db()
    trades = _load_swing_trades(path)
    os.unlink(path)
    assert trades == []


# ---- _load_intraday_trades --------------------------------------------------

def test_load_intraday_only_closed():
    path = _make_db(intraday_rows=[
        ("2026-06-12", "UBXG", "orb", 52, 7.04, 6.05, 9.02, 7.75, "stop",
         36.92, 0.72, "closed", "2026-06-12T13:00:00+00:00", "2026-06-12T16:00:00+00:00"),
        ("2026-06-19", "OPEN", "orb", 10, 10.0, 9.0, 12.0, None, None,
         None, None, "open", "2026-06-19T13:00:00+00:00", None),
    ])
    trades = _load_intraday_trades(path)
    os.unlink(path)
    assert len(trades) == 1
    assert trades[0]["symbol"] == "UBXG"


def test_load_intraday_empty_db():
    path = _make_db()
    trades = _load_intraday_trades(path)
    os.unlink(path)
    assert trades == []


# ---- print_report smoke test ------------------------------------------------

def test_print_report_runs_without_error(capsys):
    path = _make_db(
        swing_rows=[
            ("2026-06-10", "TGTX", "manual", 14, 41.66, 48.0, "target", 88.76, 1.524),
            ("2026-06-10", "KEEL", "manual", 68, 5.77, 5.0, "stop", -52.36, -1.0),
        ],
        intraday_rows=[
            ("2026-06-12", "UBXG", "orb", 52, 7.04, 6.05, 9.02, 7.75, "stop",
             36.92, 0.72, "closed", "2026-06-12T13:00:00+00:00", "2026-06-12T16:00:00+00:00"),
        ],
    )
    print_report(db_path=path)
    os.unlink(path)
    out = capsys.readouterr().out
    assert "Performance Report" in out
    assert "Swing Trades" in out
    assert "Intraday" in out
    assert "Combined" in out


def test_print_report_empty_db(capsys):
    path = _make_db()
    print_report(db_path=path)
    os.unlink(path)
    out = capsys.readouterr().out
    assert "No closed trades" in out
