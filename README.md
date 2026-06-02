# microbot 📈

A small, **honest** day/swing trading bot for Alpaca paper trading — built as a
learning project. It enforces discipline (fixed risk, a strict 2:1 reward:risk,
automatic stops), *measures* whether a strategy actually has an edge, and gives
you a dashboard to watch it all.

> **Not investment advice. No bot can guarantee profit or eliminate risk.**
> This tool enforces *risk discipline* and *measures* edges honestly. Whether a
> strategy makes money is determined by backtesting + weeks of paper trading —
> not by hope. Most retail algos do **not** have an edge; the point of this
> project is to find that out cheaply, with fake money.

---

## What it does

- Scans 30+ stocks across 6 strategies every trading day
- Scores and ranks every setup (drawdown-adjusted — deep losers penalized)
- Pushes a ranked **Watchlist** and **Live Signals** tab to Google Sheets at 9:35 AM ET
- Nothing trades without your approval (`python -m microbot.approvals`)
- Places **atomic bracket orders** on **Alpaca paper account** — entry + stop + take-profit in one shot
- Reconciles filled brackets from Alpaca into the journal every morning (real P&L, R-multiples)
- Kill switch: `python -c "from microbot.broker import Broker; Broker().close_all()"` — cancels all Alpaca orders and closes every position instantly
- Weekly optimizer proposes better parameters every Monday, you approve before anything changes

---

## The two things that bite a $500 account (read these)

- **Pattern Day Trader rule.** In a US margin account under **$25,000**, you get
  only **3 day trades per 5 business days**. So this bot defaults to **swing
  trading** (`1Day` bars, overnight holds). Don't fight the PDT rule with $500.
- **Position sizing math.** Risking 1% of $500 = **$5 per trade**. On a $400
  stock, one share already risks more than that, so the bot *skips it*. That's
  correct — it naturally pushes you toward liquid, lower-priced names. The
  default universe reflects this.

---

## "Ask me first" — the live approval gate

The end goal is the bot picking, sizing, and placing trades for you — but for
**live** trading it asks before every buy/sell. This is done by decoupling
*propose* from *execute*:

- **Paper mode**: the engine places bracket orders automatically (the point is
  unattended testing). Set `PAPER_REQUIRE_APPROVAL=true` if you want to rehearse
  the approval flow with fake money first.
- **Live mode** (`LIVE_TRADING=true`, `REQUIRE_LIVE_APPROVAL=true`): the engine
  does NOT place orders. It writes each proposed trade to an approval queue and
  notifies you. Nothing touches real money until you approve it.

Review and decide:

```bash
python -m microbot.approvals          # interactive: approve / reject each
python -m microbot.approvals --list   # just list what's pending
```

...or click ✅/❌ in the dashboard's "Trades awaiting your approval" panel.

When you approve, the trade is **re-validated against a live account snapshot**
(buying power, not-already-held) before submitting — a proposal that went stale
is rejected, not blindly sent. The bot can run unattended on a schedule and
simply leave trades waiting for your yes/no.

> Turning on full live auto-execution (`REQUIRE_LIVE_APPROVAL=false`) removes the
> human gate entirely. Don't do that until many weeks of paper trading show a
> real, positive edge — and even then, start tiny.

## Setup

**Quickstart — one command (macOS/Linux/Windows):**

```bash
python bootstrap.py
```

This creates a `.venv`, installs everything, makes your `.env`, and offers a
research-only scan. Then activate the env (`source .venv/bin/activate`, or
`.venv\Scripts\activate` on Windows) and edit `.env` with your PAPER keys.

**Or do it by hand:**

```bash
# 1. Install
pip install -r requirements.txt

# 2. Get FREE Alpaca paper keys:
#    https://app.alpaca.markets/  ->  Paper account  ->  generate API keys
cp .env.example .env
#    ...then paste your PAPER key + secret into .env (keep LIVE_TRADING=false)

# 3. Research only (no orders) — start here:
python run_research.py

# 4. Run one full cycle (places PAPER bracket orders):
python -m microbot.engine

# 5. Watch the dashboard:
streamlit run microbot/dashboard.py

# Emergency: cancel everything and flatten:
python -m microbot.engine --flatten
```

Run the engine once daily *after the close* for swing trading (a cron job or
`launchd`/Task Scheduler entry works), then check the dashboard.

---

## Built-in analyzer

`analyzer.py` reads the bot's journal directly and reports on its native fields,
with `strategy` as a first-class dimension and expectancy shown in both dollars
and R:

```bash
python run_analyzer.py
# -> printed report (win rate, expectancy $ and R, profit factor, by-strategy,
#    by-symbol, by-outcome, by-hour) plus equity_curve.png, pnl_by_strategy.png,
#    and microbot_trades.xlsx (Summary / By strategy / By symbol / Trades sheets)
```

**Adaptive feedback** — `feedback.py` uses the analyzer's `expectancy` /
`win_rate` on the journal to find strategy (and strategy+symbol) combos losing
money over a real sample (>=6 closed trades by default) and **auto-pauses** them.
The engine consults this before every order; the dashboard shows which
strategies are active vs vetoed.

> "By hour" is most meaningful in intraday mode (`BAR_TIMEFRAME=15Min`); on
> daily/swing bars the log time is the run time.

---

## The paper → live roadmap (do not skip steps)

1. **Weeks 1–2 — research only.** `python run_research.py`. Read the rankings.
   Are any strategy/symbol combos *consistently* > 0 expectancy with PF > ~1.3
   across many trades? If nothing scores, that's a real answer — don't trade.
2. **Weeks 2–6 — paper trading.** Let the engine place paper bracket orders
   daily. Watch the dashboard. You want a real sample (30+ closed trades) before
   trusting *any* number. A great week proves nothing.
3. **Reality gates before risking a real dollar:** positive expectancy over
   30+ paper trades, max drawdown you can stomach, and live paper results that
   roughly match the backtest (if they diverge wildly, the backtest was
   optimistic — trust the live paper).
4. **Go live tiny.** Only then set `LIVE_TRADING=true` (the engine forces a typed
   confirmation). Expect live to underperform paper (slippage, fills, emotions).

---

## Honest limitations

- Long-only (no shorting) in v1 — shorting a $500 account is a different beast.
- The backtester assumes the stop fills before the target on ambiguous bars
  (pessimistic) and models no slippage by default — add `slippage` cents to be
  realistic.
- Backtest results are **in-sample** and will overstate live performance.
- This is educational software. You are responsible for any account you connect.
