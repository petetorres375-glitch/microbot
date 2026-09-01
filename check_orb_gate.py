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

Also reports a "live-strategies-only" combined trend at that same checkpoint
(user asked 2026-09-01 to be flagged with this alongside the gate verdict,
after raising the possibility of rebuilding the bot if results don't
improve) — swing P&L excluding the permanently-paused/stopped ema_pullback
and manual strategies, plus ORB's full current lifetime P&L, so the decision
is made off a real trend line instead of the noisier all-time total that
still carries dead-strategy losses.

Before the sample is complete, it just logs progress and exits quietly.
"""
import sqlite3
import subprocess
from pathlib import Path

FIX_DATE = "2026-09-01"
GATE_SAMPLE_SIZE = 15
WIN_RATE_GATE = 0.45
DEAD_SWING_STRATEGIES = ("ema_pullback", "manual")
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
        gate_verdict = (
            f"PASSED — win rate back at/above the {WIN_RATE_GATE * 100:.0f}% gate. "
            "No action needed; ORB stays live."
        )
    else:
        gate_verdict = (
            f"FAILED — {win_rate * 100:.1f}% WR over the {n} trades since the "
            f"2026-09-01 too-tight-range fix, below the {WIN_RATE_GATE * 100:.0f}% "
            "gate even excluding the bug this fix addressed. Per policy, "
            "consider pulling ORB."
        )
    lines.append(gate_verdict)

    trend = _live_strategy_trend()
    lines.append(trend["log_line"])

    verdict = (
        f"ORB gate re-check: {gate_verdict}\n\n"
        f"Live-strategies-only trend: {trend['summary']}"
    )
    try:
        from microbot.notify import notify
        notify(verdict)
    except Exception as e:
        lines.append(f"(desktop notify failed: {e})")

    lines.append("Sample evaluated — removing self from crontab.")
    _log(lines)
    _self_delete()


def _live_strategy_trend() -> dict:
    """Swing P&L excluding dead/paused strategies, plus ORB's full current
    lifetime P&L — the trend line to judge against, not the all-time total
    which still carries GOOG/AMD/ema_pullback-era losses."""
    con = sqlite3.connect(DB_PATH)
    placeholders = ",".join("?" for _ in DEAD_SWING_STRATEGIES)
    swing_n, swing_wins, swing_pnl = con.execute(
        f"SELECT COUNT(*), SUM(pnl > 0), COALESCE(SUM(pnl), 0) FROM trades "
        f"WHERE strategy NOT IN ({placeholders})",
        DEAD_SWING_STRATEGIES,
    ).fetchone()
    orb_n, orb_wins, orb_pnl = con.execute(
        "SELECT COUNT(*), SUM(pnl > 0), COALESCE(SUM(pnl), 0) "
        "FROM intraday_trades WHERE status='closed'"
    ).fetchone()
    con.close()

    swing_wins, orb_wins = swing_wins or 0, orb_wins or 0
    total_n = swing_n + orb_n
    total_pnl = swing_pnl + orb_pnl
    total_wr = (swing_wins + orb_wins) / total_n if total_n else 0.0

    summary = (
        f"{total_n} trades, {total_wr * 100:.1f}% WR, ${total_pnl:,.2f} P&L "
        f"(swing ex-dead-strategies: {swing_n} trades, ${swing_pnl:,.2f}; "
        f"ORB lifetime: {orb_n} trades, ${orb_pnl:,.2f})"
    )
    log_line = f"Live-strategies-only trend: {summary}"
    return {"summary": summary, "log_line": log_line}


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
