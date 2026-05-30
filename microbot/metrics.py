"""
metrics.py
----------
Turns a list of closed trades into the stats you asked for: win, loss,
expectancy, win %, plus profit factor, average R, and an equity curve.

A "trade" dict needs at minimum: pnl (dollars), r_multiple (pnl / initial risk).
"""
from __future__ import annotations

from typing import List, Dict
import numpy as np


def compute(trades: List[Dict]) -> Dict:
    if not trades:
        return _empty()

    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    rs = np.array([t.get("r_multiple", 0.0) for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]

    n = len(pnls)
    win_rate = len(wins) / n
    avg_win = wins.mean() if len(wins) else 0.0
    avg_loss = losses.mean() if len(losses) else 0.0  # negative number

    # Expectancy = average $ you expect to make per trade.
    expectancy = pnls.mean()
    # Expectancy in R (risk units) — strategy-agnostic, the cleanest edge measure.
    expectancy_r = rs.mean()

    gross_profit = wins.sum()
    gross_loss = abs(losses.sum())
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    equity_curve = np.cumsum(pnls)
    peak = np.maximum.accumulate(equity_curve)
    drawdown = equity_curve - peak
    max_dd = drawdown.min() if len(drawdown) else 0.0

    f = lambda x: round(float(x), 2)   # native float, gspread/JSON safe
    return {
        "trades": int(n),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": round(float(win_rate), 4),
        "loss_rate": round(float(1 - win_rate), 4),
        "avg_win": f(avg_win),
        "avg_loss": f(avg_loss),
        "expectancy": f(expectancy),                 # dollars per trade
        "expectancy_r": round(float(expectancy_r), 3),  # R per trade
        "profit_factor": round(float(profit_factor), 3) if profit_factor != float("inf") else None,
        "total_pnl": f(pnls.sum()),
        "max_drawdown": f(max_dd),
        "equity_curve": [float(x) for x in equity_curve],
    }


def _empty():
    return {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "loss_rate": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "expectancy": 0.0, "expectancy_r": 0.0,
            "profit_factor": None, "total_pnl": 0.0, "max_drawdown": 0.0,
            "equity_curve": []}
