"""
notify.py
---------
Desktop notifications via notify-send (Linux). Shows a pop-up whenever
the engine runs — summary if no signals, urgent alert if trades are queued.
"""
from __future__ import annotations
import subprocess


def _desktop(title: str, body: str, urgency: str = "normal", icon: str = "dialog-information"):
    """Fire a desktop pop-up via notify-send. Silently skips if unavailable."""
    try:
        subprocess.run(
            ["notify-send", "-u", urgency, "-i", icon,
             "-a", "microbot", "-t", "10000", title, body],
            check=False
        )
    except FileNotFoundError:
        pass


def notify(message: str):
    """Called when trades are queued for approval — urgent pop-up."""
    print(f"\n🔔 {message}\n")
    _desktop("🔔 microbot — Action Required", message, urgency="critical", icon="dialog-warning")


def notify_summary(signals: int, top_candidates: list, equity: float):
    """Called at end of every engine run with a daily summary pop-up."""
    if signals > 0:
        title = f"📈 microbot — {signals} Signal{'s' if signals > 1 else ''} Today!"
        body = "\n".join(
            f"• {s['symbol']} / {s['strategy']}  entry ${s['entry']}"
            for s in top_candidates[:3]
        )
        urgency = "normal"
        icon = "dialog-information"
    else:
        title = "microbot — Daily Scan Complete"
        tops = ", ".join(
            f"{r['symbol']}/{r['strategy']}"
            for r in top_candidates[:3]
        )
        body = f"No signals today.\nTop picks: {tops}\nEquity: ${equity:,.2f}"
        urgency = "low"
        icon = "dialog-information"

    print(f"\n📊 {title}\n   {body}\n")
    _desktop(title, body, urgency=urgency, icon=icon)
