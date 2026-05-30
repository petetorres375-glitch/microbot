"""
analyzer.py
-----------
microbot's own trade analyzer. It reads the bot's SQLite journal directly (no
CSV round-trip) and reports on the bot's native fields:

    ts, symbol, strategy, qty, entry, exit, outcome, pnl, r_multiple

Two things make this bot-native rather than generic:
  * `strategy` is a first-class dimension — "P&L by strategy" tells you which of
    trend_momentum / mean_reversion / breakout is actually paying.
  * `r_multiple` is recorded per trade, so we report expectancy in BOTH dollars
    and R (risk units), which is the cleaner, account-size-independent edge.

Outputs: a printed report, an equity-curve chart, a P&L-by-strategy chart, and a
multi-sheet Excel workbook. The overall stats reuse metrics.compute() so the
analyzer, dashboard, and screener all agree on the numbers.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from . import journal, metrics


# ----------------------------------------------------------------------
#  DATA
# ----------------------------------------------------------------------
def frame(df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Load closed trades from the journal (or use a supplied df) and add
    derived columns: timestamp, hour, win."""
    if df is None:
        df = pd.DataFrame(journal.fetch_trades())
    if df.empty:
        return df
    df = df.copy()
    if "ts" in df:
        df["timestamp"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        df["hour"] = df["timestamp"].dt.hour
    df["win"] = df["pnl"] > 0
    return df


# ----------------------------------------------------------------------
#  SMALL CALCULATIONS (used by feedback.py for the adaptive veto loop)
# ----------------------------------------------------------------------
def expectancy(df: pd.DataFrame) -> float:
    """Average $ P&L per trade."""
    return float(df["pnl"].mean()) if len(df) else 0.0


def expectancy_r(df: pd.DataFrame) -> float:
    """Average R per trade (account-size independent)."""
    return float(df["r_multiple"].mean()) if "r_multiple" in df and len(df) else 0.0


def win_rate(df: pd.DataFrame) -> float:
    """Win rate as a percentage."""
    return float((df["pnl"] > 0).mean() * 100) if len(df) else 0.0


def profit_factor(df: pd.DataFrame) -> Optional[float]:
    """Gross profit / gross loss, or None if there are no losing trades."""
    gp = df.loc[df["pnl"] > 0, "pnl"].sum()
    gl = abs(df.loc[df["pnl"] < 0, "pnl"].sum())
    return float(gp / gl) if gl > 0 else None


# ----------------------------------------------------------------------
#  GROUPED BREAKDOWNS
# ----------------------------------------------------------------------
def _group(df: pd.DataFrame, key) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    g = df.groupby(key)
    out = g["pnl"].agg(trades="count", total_pnl="sum", expectancy="mean").round(2)
    out["win_rate"] = (g["win"].mean() * 100).round(1)
    if "r_multiple" in df:
        out["expectancy_r"] = g["r_multiple"].mean().round(3)
    return out.sort_values("total_pnl", ascending=False)


def by_strategy(df): return _group(df, "strategy")
def by_symbol(df):   return _group(df, "symbol")
def by_outcome(df):  return _group(df, "outcome")
def by_hour(df):     return _group(df, "hour") if "hour" in df else pd.DataFrame()


# ----------------------------------------------------------------------
#  SUMMARY (delegates to metrics so all components agree)
# ----------------------------------------------------------------------
def summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return metrics.compute([])
    m = metrics.compute(df.to_dict("records"))
    m["largest_win"] = round(float(df["pnl"].max()), 2)
    m["largest_loss"] = round(float(df["pnl"].min()), 2)
    return m


# ----------------------------------------------------------------------
#  CHARTS
# ----------------------------------------------------------------------
def chart_equity_curve(df: pd.DataFrame, path: str = "equity_curve.png"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ordered = df.sort_values("timestamp") if "timestamp" in df else df
    equity = ordered["pnl"].cumsum()
    plt.figure()
    plt.plot(equity.values, marker="o")
    plt.title("Equity Curve (Cumulative P&L)")
    plt.xlabel("Trade number"); plt.ylabel("Cumulative P&L ($)")
    plt.axhline(0, color="gray", linewidth=0.8)
    plt.tight_layout(); plt.savefig(path); plt.close()
    return path


def chart_pnl_by_strategy(df: pd.DataFrame, path: str = "pnl_by_strategy.png"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    totals = df.groupby("strategy")["pnl"].sum()
    plt.figure()
    totals.plot(kind="bar", color=np.where(totals >= 0, "#2e7d32", "#c62828"))
    plt.title("Total P&L by Strategy")
    plt.xlabel("Strategy"); plt.ylabel("P&L ($)")
    plt.axhline(0, color="gray", linewidth=0.8)
    plt.tight_layout(); plt.savefig(path); plt.close()
    return path


# ----------------------------------------------------------------------
#  EXPORT
# ----------------------------------------------------------------------
def export_excel(df: pd.DataFrame, path: str = "microbot_trades.xlsx"):
    m = summary(df)
    # Excel can't store timezone-aware datetimes — make a tz-naive copy.
    trades = df.copy()
    for col in trades.columns:
        if pd.api.types.is_datetime64tz_dtype(trades[col]):
            trades[col] = trades[col].dt.tz_localize(None)
    summary_tbl = pd.DataFrame({
        "Metric": ["Trades", "Win rate (%)", "Wins", "Losses",
                   "Expectancy ($)", "Expectancy (R)", "Profit factor",
                   "Largest win ($)", "Largest loss ($)", "Avg win ($)",
                   "Avg loss ($)", "Total P&L ($)", "Max drawdown ($)"],
        "Value": [m["trades"], m["win_rate"] * 100, m["wins"], m["losses"],
                  m["expectancy"], m["expectancy_r"],
                  m["profit_factor"] if m["profit_factor"] is not None else "N/A",
                  m.get("largest_win", 0), m.get("largest_loss", 0),
                  m["avg_win"], m["avg_loss"], m["total_pnl"], m["max_drawdown"]],
    })
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        summary_tbl.to_excel(w, sheet_name="Summary", index=False)
        by_strategy(df).to_excel(w, sheet_name="By strategy")
        by_symbol(df).to_excel(w, sheet_name="By symbol")
        trades.to_excel(w, sheet_name="Trades", index=False)
    return path


# ----------------------------------------------------------------------
#  ONE-CALL TEXT REPORT
# ----------------------------------------------------------------------
def print_report(df: Optional[pd.DataFrame] = None, make_charts=True, make_excel=True):
    df = frame(df)
    if df.empty:
        print("No closed trades in the journal yet — nothing to analyze.")
        return df

    m = summary(df)
    print("\n=== microbot trade report ===")
    print(f"Trades: {m['trades']}   Win rate: {m['win_rate']*100:.1f}%   "
          f"({m['wins']}W / {m['losses']}L)")
    print(f"Expectancy: ${m['expectancy']:.2f} / trade  ({m['expectancy_r']} R)")
    pf = m["profit_factor"]
    print(f"Profit factor: {pf if pf is not None else 'N/A'}   "
          f"Total P&L: ${m['total_pnl']:.2f}   Max DD: ${m['max_drawdown']:.2f}")
    print(f"Largest win: ${m.get('largest_win',0):.2f}   "
          f"Largest loss: ${m.get('largest_loss',0):.2f}")

    print("\n--- By strategy ---");  print(by_strategy(df).to_string())
    print("\n--- By symbol ---");    print(by_symbol(df).to_string())
    print("\n--- By outcome ---");   print(by_outcome(df).to_string())
    if "hour" in df and df["hour"].notna().any():
        print("\n--- By hour (most useful in intraday mode) ---")
        print(by_hour(df).to_string())

    if make_charts:
        print("\nSaved:", chart_equity_curve(df), "+", chart_pnl_by_strategy(df))
    if make_excel:
        print("Saved:", export_excel(df))
    return df
