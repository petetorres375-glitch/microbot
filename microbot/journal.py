"""
journal.py
----------
A tiny SQLite trade journal. The dashboard reads from this. We log:
  * signals    - every signal the bot generated (even if not taken)
  * orders     - orders we actually submitted (with the Alpaca order id)
  * trades     - CLOSED trades with realized pnl + r_multiple (fills metrics)

SQLite is built into Python, zero setup, and is a great thing to learn.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, List

from .config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, symbol TEXT, strategy TEXT, side TEXT,
    entry REAL, stop REAL, target REAL, atr REAL, reason TEXT, taken INTEGER
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, alpaca_id TEXT, symbol TEXT, strategy TEXT, side TEXT,
    qty INTEGER, entry REAL, stop REAL, target REAL,
    dollar_risk REAL, notional REAL, status TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, symbol TEXT, strategy TEXT, qty INTEGER,
    entry REAL, exit REAL, outcome TEXT, pnl REAL, r_multiple REAL
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, symbol TEXT, strategy TEXT, side TEXT, qty INTEGER,
    entry REAL, stop REAL, target REAL, dollar_risk REAL, notional REAL,
    score REAL, reason TEXT,
    status TEXT DEFAULT 'pending',   -- pending | submitted | rejected | error
    decided_ts TEXT, alpaca_id TEXT, note TEXT
);
"""


@contextmanager
def _conn():
    con = sqlite3.connect(settings.db_path)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init():
    with _conn() as con:
        con.executescript(SCHEMA)


def _now():
    return datetime.now(timezone.utc).isoformat()


def log_signal(sig, taken: bool):
    with _conn() as con:
        con.execute(
            "INSERT INTO signals(ts,symbol,strategy,side,entry,stop,target,atr,reason,taken)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (_now(), sig.symbol, sig.strategy, sig.side, sig.entry, sig.stop,
             sig.target, sig.atr, sig.reason, int(taken)),
        )


def log_order(trade, alpaca_id: str, status: str):
    r = trade.as_row()
    with _conn() as con:
        con.execute(
            "INSERT INTO orders(ts,alpaca_id,symbol,strategy,side,qty,entry,stop,"
            "target,dollar_risk,notional,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), alpaca_id, r["symbol"], r["strategy"], r["side"], r["qty"],
             r["entry"], r["stop"], r["target"], r["dollar_risk"], r["notional"], status),
        )


def log_closed_trade(t: Dict):
    with _conn() as con:
        con.execute(
            "INSERT INTO trades(ts,symbol,strategy,qty,entry,exit,outcome,pnl,r_multiple)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (_now(), t["symbol"], t["strategy"], t.get("qty", 0), t["entry"],
             t["exit"], t["outcome"], t["pnl"], t["r_multiple"]),
        )


def fetch_trades() -> List[Dict]:
    with _conn() as con:
        return [dict(r) for r in con.execute("SELECT * FROM trades ORDER BY ts")]


def fetch_signals(limit: int = 200) -> List[Dict]:
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM signals ORDER BY ts DESC LIMIT ?", (limit,))]


def fetch_orders(limit: int = 200) -> List[Dict]:
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM orders ORDER BY ts DESC LIMIT ?", (limit,))]


# ---- approval queue (human-in-the-loop for live trading) ----
def enqueue_approval(trade, score: float = 0.0) -> int:
    r = trade.as_row()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO approvals(ts,symbol,strategy,side,qty,entry,stop,target,"
            "dollar_risk,notional,score,reason,status) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'pending')",
            (_now(), r["symbol"], r["strategy"], r["side"], r["qty"], r["entry"],
             r["stop"], r["target"], r["dollar_risk"], r["notional"], score,
             r["reason"]),
        )
        return cur.lastrowid


def fetch_pending() -> List[Dict]:
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM approvals WHERE status='pending' ORDER BY score DESC, ts")]


def fetch_approvals(limit: int = 100) -> List[Dict]:
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM approvals ORDER BY ts DESC LIMIT ?", (limit,))]


def get_approval(approval_id: int) -> Dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        return dict(row) if row else None


def set_approval_status(approval_id: int, status: str,
                        alpaca_id: str = None, note: str = None):
    with _conn() as con:
        con.execute(
            "UPDATE approvals SET status=?, decided_ts=?, alpaca_id=?, note=? WHERE id=?",
            (status, _now(), alpaca_id, note, approval_id),
        )
