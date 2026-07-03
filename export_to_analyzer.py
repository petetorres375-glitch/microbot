"""
export_to_analyzer.py
---------------------
Exports closed trades from microbot's journal (microbot.db) to a CSV file
that matches the trade_analyzer's expected format:
  date, time, symbol, setup, direction, entry, exit, shares

Rows with pnl = 0 are excluded — these are no-fill orders and manual closes
where the real exit price wasn't captured (entry price was reused as a
placeholder), not real breakeven trades. microbot's own analyzer.py applies
the same filter, so both tools agree on trade count and win rate.

Run from the microbot directory:
    python export_to_analyzer.py
    python export_to_analyzer.py --out ~/projects/trade_analyzer/trades.csv
"""
import argparse
import csv
import sqlite3
from pathlib import Path


MICROBOT_DB = Path(__file__).parent / "microbot.db"
DEFAULT_OUT = Path.home() / "projects/trade_analyzer/trades.csv"


def export(db_path: Path, out_path: Path):
    if not db_path.exists():
        print(f"No database found at {db_path} — run the engine first.")
        return

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM trades WHERE pnl != 0 ORDER BY ts").fetchall()
    con.close()

    if not rows:
        print("No closed trades in the journal yet.")
        return

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "time", "symbol", "setup", "direction", "entry", "exit", "shares"])
        for r in rows:
            ts = r["ts"][:16]          # "2026-05-30T21:30"
            date = ts[:10]             # "2026-05-30"
            time = ts[11:].replace("T", "")   # "21:30"
            writer.writerow([
                date,
                time,
                r["symbol"],
                r["strategy"],         # setup
                "long",                # microbot v1 is long-only
                r["entry"],
                r["exit"],
                r["qty"],              # shares
            ])

    print(f"Exported {len(rows)} trades → {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="Output CSV path (default: ~/projects/trade_analyzer/trades.csv)")
    args = parser.parse_args()
    export(MICROBOT_DB, Path(args.out))
