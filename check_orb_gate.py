"""
Recurring check for whether ORB's win rate recovers above the 45% performance
gate after the 2026-09-01 too-tight-ORB-range fix (commit 2c0ae69, STDN
-1.79R/-$892.75 — see CLAUDE.md "Too-tight ORB range fix").

Runs weekdays via crontab, after the day's ORB session is done. Counts ORB
trades CLOSED after 2026-09-01 (the fix date) — trades from before that date
may include the bug this fix addressed and shouldn't count toward judging it.
Once GATE_SAMPLE_SIZE post-fix trades have closed, evaluates the win rate
over just that sample and reports a verdict, then self-deletes (removes its
own crontab line and this file) — this is a one-shot re-check, not an
indefinite watch.

Before the sample is complete, it just logs progress and exits quietly.
"""
import sqlite3
import subprocess
from pathlib import Path

FIX_DATE = "2026-09-01"
GATE_SAMPLE_SIZE = 15
WIN_RATE_GATE = 0.45
DB_PATH = Path(__file__).parent / "microbot.db"
LOG_PATH = Path(__file__).parent / "orb_gate_check.log"
SELF = Path(__file__)


def main():
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT symbol, pnl, r_multiple FROM intraday_trades "
        "WHERE status='closed' AND date > ? ORDER BY ts_close",
        (FIX_DATE,),
    ).fetchall()
    con.close()

    n = len(rows)
    lines = [f"=== ORB gate re-check ({n}/{GATE_SAMPLE_SIZE} post-fix trades) ==="]

    if n < GATE_SAMPLE_SIZE:
        lines.append(f"Waiting — {GATE_SAMPLE_SIZE - n} more post-fix trade(s) needed.")
        _log(lines)
        return

    wins = sum(1 for _, pnl, _ in rows if pnl > 0)
    win_rate = wins / n
    total_pnl = sum(pnl for _, pnl, _ in rows)
    lines.append(
        f"Sample complete: {n} trades, {wins}W/{n - wins}L, "
        f"{win_rate * 100:.1f}% WR, ${total_pnl:,.2f} P&L"
    )

    if win_rate >= WIN_RATE_GATE:
        lines.append(
            f"PASSED — win rate back at/above the {WIN_RATE_GATE * 100:.0f}% gate. "
            "No action needed; ORB stays live."
        )
    else:
        verdict = (
            f"ORB gate re-check FAILED: {win_rate * 100:.1f}% WR over the "
            f"{n} trades since the 2026-09-01 too-tight-range fix — "
            f"below the {WIN_RATE_GATE * 100:.0f}% gate even excluding the "
            "bug this fix addressed. Per policy, consider pulling ORB."
        )
        lines.append(verdict)
        try:
            from microbot.notify import notify
            notify(verdict)
        except Exception as e:
            lines.append(f"(desktop notify failed: {e})")

    lines.append("Sample evaluated — removing self from crontab.")
    _log(lines)
    _self_delete()


def _log(lines):
    with open(LOG_PATH, "a") as f:
        for line in lines:
            print(line)
            f.write(line + "\n")


def _self_delete():
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    remaining = "\n".join(
        l for l in result.stdout.splitlines() if "check_orb_gate.py" not in l
    )
    subprocess.run(["crontab", "-"], input=remaining + "\n", text=True)
    SELF.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
