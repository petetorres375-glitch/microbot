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
    dollar_risk REAL, notional REAL, status TEXT,
    closed INTEGER DEFAULT 0
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
CREATE TABLE IF NOT EXISTS split_adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT, split_type TEXT, ratio REAL, ex_date TEXT,
    applied_ts TEXT
);
CREATE TABLE IF NOT EXISTS active_params (
    strategy TEXT PRIMARY KEY,
    params_json TEXT,
    promoted_ts TEXT
);
CREATE TABLE IF NOT EXISTS param_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, strategy TEXT,
    proposed_params_json TEXT, current_params_json TEXT,
    is_score REAL, oos_score REAL, current_oos_score REAL,
    improvement_pct REAL,
    status TEXT DEFAULT 'pending',
    decided_ts TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS ipo_discovered (
    symbol TEXT PRIMARY KEY,
    discovered_ts TEXT,
    status TEXT DEFAULT 'active'   -- active | rejected
);
CREATE TABLE IF NOT EXISTS scan_log (
    key TEXT PRIMARY KEY,
    ts TEXT
);
CREATE TABLE IF NOT EXISTS intraday_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    symbol TEXT,
    strategy TEXT DEFAULT 'orb',
    qty INTEGER,
    entry REAL,
    stop REAL,
    target REAL,
    half_exit_price REAL,
    exit_price REAL,
    exit_reason TEXT,
    pnl REAL,
    r_multiple REAL,
    status TEXT DEFAULT 'open',
    alpaca_entry_id TEXT,
    alpaca_stop_id TEXT,
    ts_open TEXT,
    ts_close TEXT
);
CREATE TABLE IF NOT EXISTS intraday_daily (
    date TEXT PRIMARY KEY,
    realized_pnl REAL DEFAULT 0,
    trades_taken INTEGER DEFAULT 0,
    halted INTEGER DEFAULT 0
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
        # migrate existing DBs that predate the `closed` column
        cols = {r[1] for r in con.execute("PRAGMA table_info(orders)")}
        if "closed" not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN closed INTEGER DEFAULT 0")


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


def fetch_open_orders() -> List[Dict]:
    """Return orders not yet reconciled into a closed trade."""
    with _conn() as con:
        rows = con.execute(
            "SELECT alpaca_id AS order_id, symbol, strategy, qty, entry, stop, target"
            " FROM orders WHERE closed = 0 ORDER BY ts"
        ).fetchall()
        return [dict(r) for r in rows]


def mark_order_closed(order_id: str) -> None:
    with _conn() as con:
        con.execute("UPDATE orders SET closed = 1 WHERE alpaca_id = ?", (order_id,))


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


# ---- split adjustment helpers ----

def all_symbols() -> List[str]:
    """All unique symbols that appear anywhere in the journal."""
    with _conn() as con:
        rows = con.execute(
            "SELECT DISTINCT symbol FROM orders "
            "UNION SELECT DISTINCT symbol FROM approvals "
            "UNION SELECT DISTINCT symbol FROM signals"
        ).fetchall()
    return [r["symbol"] for r in rows if r["symbol"]]


def split_already_applied(symbol: str, ex_date) -> bool:
    ex_str = ex_date.isoformat() if hasattr(ex_date, "isoformat") else str(ex_date)
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM split_adjustments WHERE symbol=? AND ex_date=?",
            (symbol, ex_str),
        ).fetchone()
    return row is not None


def apply_split(symbol: str, ratio: float, ex_date, split_type: str) -> int:
    """
    Rescale price and qty in orders, approvals, and signals logged before ex_date.

    Forward split (e.g. 2:1): prices ÷ ratio, qty × ratio.
    Reverse split (e.g. 1:10): prices × ratio, qty ÷ ratio (floor to 1 min).
    Returns total rows updated.
    """
    ex_str = ex_date.isoformat() if hasattr(ex_date, "isoformat") else str(ex_date)
    if split_type == "forward":
        price_mult = 1.0 / ratio
        qty_mult = ratio
    else:
        price_mult = ratio
        qty_mult = 1.0 / ratio

    total = 0
    with _conn() as con:
        cur = con.execute(
            "UPDATE orders SET "
            "entry=ROUND(entry*?,4), stop=ROUND(stop*?,4), "
            "target=ROUND(target*?,4), qty=MAX(1,ROUND(qty*?)) "
            "WHERE symbol=? AND ts<?",
            (price_mult, price_mult, price_mult, qty_mult, symbol, ex_str),
        )
        total += cur.rowcount
        cur = con.execute(
            "UPDATE approvals SET "
            "entry=ROUND(entry*?,4), stop=ROUND(stop*?,4), "
            "target=ROUND(target*?,4), qty=MAX(1,ROUND(qty*?)) "
            "WHERE symbol=? AND ts<?",
            (price_mult, price_mult, price_mult, qty_mult, symbol, ex_str),
        )
        total += cur.rowcount
        cur = con.execute(
            "UPDATE signals SET "
            "entry=ROUND(entry*?,4), stop=ROUND(stop*?,4), "
            "target=ROUND(target*?,4) "
            "WHERE symbol=? AND ts<?",
            (price_mult, price_mult, price_mult, symbol, ex_str),
        )
        total += cur.rowcount
    return total


def mark_split_applied(symbol: str, ratio: float, ex_date, split_type: str):
    ex_str = ex_date.isoformat() if hasattr(ex_date, "isoformat") else str(ex_date)
    with _conn() as con:
        con.execute(
            "INSERT INTO split_adjustments(symbol,split_type,ratio,ex_date,applied_ts)"
            " VALUES(?,?,?,?,?)",
            (symbol, split_type, ratio, ex_str, _now()),
        )


# ---- parameter proposal / active params ----

def fetch_active_params() -> Dict:
    """Returns {strategy_name: params_dict} for all promoted strategies."""
    import json as _json
    with _conn() as con:
        rows = con.execute("SELECT strategy, params_json FROM active_params").fetchall()
    return {r["strategy"]: _json.loads(r["params_json"]) for r in rows}


def save_param_proposal(p: Dict) -> int:
    """Saves an optimizer proposal. Returns the new proposal id."""
    import json as _json
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO param_proposals"
            "(ts,strategy,proposed_params_json,current_params_json,"
            "is_score,oos_score,current_oos_score,improvement_pct,status)"
            " VALUES(?,?,?,?,?,?,?,?,'pending')",
            (_now(), p["strategy"],
             _json.dumps(p["proposed_params"]),
             _json.dumps(p["current_params"]),
             p["is_score"], p["oos_score"],
             p["current_oos_score"], p["improvement_pct"]),
        )
        return cur.lastrowid


def fetch_pending_proposals() -> List[Dict]:
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM param_proposals WHERE status='pending' ORDER BY improvement_pct DESC"
        )]


def get_proposal(proposal_id: int) -> Dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM param_proposals WHERE id=?", (proposal_id,)
        ).fetchone()
    return dict(row) if row else None


def approve_param_proposal(proposal_id: int) -> bool:
    """Promote proposed params to active_params. Returns True on success."""
    import json as _json
    p = get_proposal(proposal_id)
    if not p or p["status"] != "pending":
        return False
    with _conn() as con:
        con.execute(
            "INSERT INTO active_params(strategy,params_json,promoted_ts)"
            " VALUES(?,?,?) ON CONFLICT(strategy) DO UPDATE SET"
            " params_json=excluded.params_json, promoted_ts=excluded.promoted_ts",
            (p["strategy"], p["proposed_params_json"], _now()),
        )
        con.execute(
            "UPDATE param_proposals SET status='approved', decided_ts=? WHERE id=?",
            (_now(), proposal_id),
        )
    return True


def reject_param_proposal(proposal_id: int, note: str = ""):
    with _conn() as con:
        con.execute(
            "UPDATE param_proposals SET status='rejected', decided_ts=?, note=? WHERE id=?",
            (_now(), note, proposal_id),
        )


# ---- IPO discovery cache ----

def fetch_known_ipos() -> List[str]:
    with _conn() as con:
        return [r["symbol"] for r in con.execute(
            "SELECT symbol FROM ipo_discovered WHERE status='active' ORDER BY discovered_ts"
        ).fetchall()]


def fetch_rejected_ipos() -> List[str]:
    with _conn() as con:
        return [r["symbol"] for r in con.execute(
            "SELECT symbol FROM ipo_discovered WHERE status='rejected'"
        ).fetchall()]


def add_discovered_ipo(symbol: str):
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO ipo_discovered(symbol, discovered_ts, status)"
            " VALUES(?, ?, 'active')",
            (symbol.upper(), _now()),
        )


def reject_discovered_ipo(symbol: str):
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO ipo_discovered(symbol, discovered_ts, status)"
            " VALUES(?, ?, 'rejected')",
            (symbol.upper(), _now()),
        )


def get_scan_log(key: str) -> str | None:
    with _conn() as con:
        row = con.execute("SELECT ts FROM scan_log WHERE key=?", (key,)).fetchone()
    return row["ts"] if row else None


def set_scan_log(key: str):
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO scan_log(key, ts) VALUES(?, ?)",
            (key, _now()),
        )
