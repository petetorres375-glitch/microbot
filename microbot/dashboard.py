"""
dashboard.py
------------
A Streamlit dashboard — Schwab-style at-a-glance metrics on top, then the detail.
Run with:   streamlit run microbot/dashboard.py

Shows:
  * Account header: equity, buying power, day trades used, PDT flag
  * Performance: win, loss, win %, expectancy ($ and R), profit factor, max DD
  * Equity curve (cumulative realized P/L)
  * Open positions table (live from Alpaca)
  * Per-ticker breakdown (win/loss/expectancy by symbol)
  * Latest research rankings + live signals
  * Recent signals & orders log

It reads from the SQLite journal and (if keys are set) live Alpaca data. It is
read-only — it never places orders.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from microbot import journal, metrics
from microbot.config import settings

st.set_page_config(page_title="microbot dashboard", layout="wide")
journal.init()


def _safe_account():
    try:
        from microbot.broker import Broker
        b = Broker()
        return b.account(), b.positions()
    except Exception as e:
        st.warning(f"Live Alpaca data unavailable ({e}). Showing journal only.")
        return None, []


def _per_ticker(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty:
        return pd.DataFrame()
    rows = []
    for sym, g in trades_df.groupby("symbol"):
        m = metrics.compute(g.to_dict("records"))
        rows.append({
            "ticker": sym, "trades": m["trades"], "wins": m["wins"],
            "losses": m["losses"], "win_%": round(m["win_rate"] * 100, 1),
            "expectancy_$": m["expectancy"], "expectancy_R": m["expectancy_r"],
            "profit_factor": m["profit_factor"], "total_$": m["total_pnl"],
        })
    return pd.DataFrame(rows).sort_values("total_$", ascending=False)


# ---------------- HEADER ----------------
st.title("📈 microbot — paper trading dashboard")
acct, positions = _safe_account()

c = st.columns(5)
if acct:
    c[0].metric("Equity", f"${acct['equity']:,.2f}")
    c[1].metric("Buying Power", f"${acct['buying_power']:,.2f}")
    c[2].metric("Cash", f"${acct['cash']:,.2f}")
    c[3].metric("Day Trades (5d)", acct["daytrade_count"])
    c[4].metric("PDT Flag", "YES" if acct["pattern_day_trader"] else "no")
else:
    c[0].metric("Starting Equity", f"${settings.starting_equity:,.2f}")

st.caption(f"Mode: {'LIVE' if settings.live_trading else 'PAPER'}  ·  "
           f"Risk/trade: {settings.risk_per_trade_pct*100:.1f}%  ·  "
           f"Reward:Risk {settings.reward_risk_ratio:.0f}:1")

# ---------------- PERFORMANCE ----------------
trades = journal.fetch_trades()
trades_df = pd.DataFrame(trades)
m = metrics.compute(trades)

st.subheader("Performance (closed trades)")
p = st.columns(6)
p[0].metric("Trades", m["trades"])
p[1].metric("Win %", f"{m['win_rate']*100:.1f}%")
p[2].metric("Wins / Losses", f"{m['wins']} / {m['losses']}")
p[3].metric("Expectancy", f"${m['expectancy']:.2f}", f"{m['expectancy_r']} R")
p[4].metric("Profit Factor", m["profit_factor"] if m["profit_factor"] else "—")
p[5].metric("Max Drawdown", f"${m['max_drawdown']:.2f}")

if m["equity_curve"]:
    st.line_chart(pd.DataFrame({"cumulative P/L ($)": m["equity_curve"]}))

# ---------------- PENDING APPROVALS ("ask me first") ----------------
st.subheader("⏳ Trades awaiting your approval")
pend = journal.fetch_pending()
if not pend:
    st.info("No trades waiting. (Live trades queue here for your yes/no.)")
else:
    st.caption("These are proposed but NOT placed. Approving submits a real order "
               "(re-validated against your live account first).")
    from microbot import approvals
    for a in pend:
        cols = st.columns([3, 1, 1])
        cols[0].markdown(
            f"**#{a['id']} · {a['qty']}× {a['symbol']}** ({a['strategy']})  \n"
            f"entry ~{a['entry']} · stop {a['stop']} · target {a['target']} · "
            f"risk ${a['dollar_risk']:.2f} · score {a['score']}  \n"
            f"<span style='color:gray'>{a['reason']}</span>", unsafe_allow_html=True)
        if cols[1].button("✅ Approve", key=f"ap{a['id']}"):
            res = approvals.approve(a["id"])
            (st.success if res["ok"] else st.error)(res["msg"])
            st.rerun()
        if cols[2].button("❌ Reject", key=f"rj{a['id']}"):
            approvals.reject(a["id"])
            st.rerun()

# ---------------- OPEN POSITIONS ----------------
st.subheader("Open positions")
if positions:
    st.dataframe(pd.DataFrame(positions), use_container_width=True)
else:
    st.info("No open positions (or live data unavailable).")

# ---------------- PER-TICKER BREAKDOWN ----------------
st.subheader("Per-ticker breakdown")
pt = _per_ticker(trades_df)
st.dataframe(pt if not pt.empty else pd.DataFrame({"info": ["no closed trades yet"]}),
             use_container_width=True)

# ---------------- STRATEGY (SETUP) BREAKDOWN + ANALYZER VETOES ----------------
st.subheader("Strategy breakdown (via your analyzer)")
try:
    from microbot import feedback
    v = feedback.compute_vetoes()
    if not v["table"].empty:
        tbl = v["table"].copy()
        tbl["status"] = tbl["key"].apply(
            lambda k: "🚫 vetoed" if k in v["setups"] else "✅ active")
        st.dataframe(tbl, use_container_width=True)
        if v["setups"]:
            st.warning(f"Auto-paused setups (negative expectancy): {', '.join(v['setups'])}")
    else:
        st.info("Not enough closed trades per setup yet for the analyzer to judge.")
except Exception as e:
    st.info(f"Analyzer breakdown unavailable: {e}")

# ---------------- LOGS ----------------
left, right = st.columns(2)
with left:
    st.subheader("Recent signals")
    sigs = journal.fetch_signals(50)
    st.dataframe(pd.DataFrame(sigs) if sigs else pd.DataFrame({"info": ["none"]}),
                 use_container_width=True, height=300)
with right:
    st.subheader("Recent orders")
    ords = journal.fetch_orders(50)
    st.dataframe(pd.DataFrame(ords) if ords else pd.DataFrame({"info": ["none"]}),
                 use_container_width=True, height=300)

st.caption("Mostly read-only. The one action here is approving a queued trade, "
           "which submits a real order after re-validating your account. "
           "Not investment advice.")
