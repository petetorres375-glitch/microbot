# microbot — Claude Code Context

## What this project is

A Python paper-trading bot connected to Alpaca's paper API. Starting equity ~$500 (paper account shows $100k Alpaca default). Swing-trading focus, long-only, no day trading.

## Key design principle

**The morning CCR analysis is the decision layer — the bot executes.**

The 10am ET CCR routine web-searches news on every symbol and delivers CLEAN/CAUTION/AVOID verdicts. Those verdicts are the sole gate for swing trades:

- **CLEAN** → signal auto-executes when the engine runs (no human approval needed)
- **Anything else** (CAUTION, AVOID, no verdict, stale file) → skipped entirely

The bot's job is to:
- Scan 30+ symbols across multiple strategies 24/7
- Size positions correctly and never forget a stop
- Auto-execute CLEAN signals without requiring the user to be present
- Never trade a symbol the morning analysis flagged

**Intraday (ORB)** is fully automated independently — scanner runs at 9:20 AM ET, no verdicts needed.

`python -m microbot.approvals` is still available but no longer part of the normal flow.

## Universes

- **Main universe** (`UNIVERSE` env var): momentum/growth stocks affordable on ~$500
- **Dividend universe** (`DIVIDEND_UNIVERSE`): income-focused, lower-beta names (VZ, MO, BTI, ET, AGNC, NLY, EPD, KMI, STAG, ABBV, CVX, O) — toggle with `INCLUDE_DIVIDEND_STOCKS`
- **Split universe** (`SPLIT_UNIVERSE`): post-split momentum names now affordable (NVDA, TSLA, AMZN, GOOG, SHOP) — toggle with `INCLUDE_SPLIT_STOCKS`
- **IPO universe** (`IPO_UNIVERSE`): recent IPOs with limited history, scanned with a shorter 180-day lookback — toggle with `INCLUDE_IPO_STOCKS`, tune lookback with `IPO_LOOKBACK_DAYS`. Auto-discovered via SEC EDGAR 8-A12B filings + Alpaca validation; cached in DB, rescanned every 24h. Manually add extra tickers via `IPO_UNIVERSE=`.

## Strategies

| Name | Edge | Best for |
|---|---|---|
| `trend_momentum` | EMA cross + ADX filter | Growth/momentum stocks |
| `mean_reversion` | RSI + Bollinger dip in uptrend | Liquid swing trades |
| `breakout` | Donchian + volume confirmation | Breakout momentum |
| `dividend_momentum` | Slow EMA (50/100), relaxed ADX, RSI < 65 | Low-beta dividend payers |
| `ema_pullback` | Triple EMA alignment (21>50>150) + pullback on low volume | Stage 2 uptrend setups |
| `breakout_52w` | 200-day high + 1.5x volume | Institutional-grade breakouts |
| `rsi2_reversion` | Connors RSI(2) ≤ 10 above 200 SMA, 1:1 RR off 3x ATR stop | High win-rate dips in liquid uptrends |

`rsi2_reversion` keeps its own 1:1 / 3x ATR bracket (validated 2026-06-12: 532 trades, 60% WR, +0.186R over 3.5y) — the builders deliberately do not pass the global `rr` to it. A Minervini-style VCP breakout was tested the same day and rejected (7 trades in 3.5y, all losers). `LOOKBACK_DAYS` was raised 400 → 1100 because 200-bar-warmup strategies could never accumulate the 8 backtest trades needed for a screener score; full research scans take noticeably longer as a result.

## Scheduled CCR routines

| Routine | ID | Schedule | Purpose |
|---|---|---|---|
| Pre-Market Diagnostics | `trig_01RGqaa5TuyTVHn2ThGDmxSg` | Weekdays 7:30 AM ET | Full system check: credentials, Alpaca, open position stop audit, DB, git, core imports. GO/NO-GO verdict with ~2 hours to fix before trading starts |
| Morning signal analysis | `trig_019TFaNMJyiH1atY2kykNHGD` | Weekdays 8:30 AM ET | Web-searches news on universe, delivers CLEAN/CAUTION/AVOID verdicts, pushes `morning_verdicts.json` to repo |
| Intraday pre-market scanner | `trig_01TX4CDGSGMLscLLgtkgeKAr` | Weekdays 9:15 AM ET | Runs gap scanner, web-searches news on candidates, prints CLEAN/MIXED/AVOID DAY briefing |
| Daily research scan | `trig_019qsZJECstukLDhqDFXcv6R` | Weekdays 9:35 AM ET | Runs `run_research.py`, pushes ranked candidates + live signals to Google Sheets |
| Weekly optimizer | `trig_01PYxALzYVnZuA88Kpror5Qo` | Mondays 9:00 AM ET | Walk-forward grid search, pushes `optimizer_proposals.json` to repo if improvements found |

View routine results at: https://claude.ai/code/routines

## Local cron jobs (execution layer)

CCR routines handle analysis and research. **Execution (actual order placement) runs locally** — the CCR container's network sandbox blocks outbound connections to Alpaca, so the engine must run on the local machine where Alpaca API is reachable.

```
# crontab -l
SHELL=/bin/bash
35 9 * * 1-5 cd /home/lenovo-home/microbot && git pull --quiet && source .venv/bin/activate && python -m microbot.engine >> /home/lenovo-home/microbot/engine.log 2>&1
34 9 * * 1-5 cd /home/lenovo-home/microbot && git pull --quiet && source .venv/bin/activate && python run_intraday.py >> /home/lenovo-home/microbot/intraday.log 2>&1
36 9 * * 1-5 cd /home/lenovo-home/microbot && source .venv/bin/activate && python -m microbot.trail >> /home/lenovo-home/microbot/trail.log 2>&1
30 10-15 * * 1-5 cd /home/lenovo-home/microbot && source .venv/bin/activate && python -m microbot.trail >> /home/lenovo-home/microbot/trail.log 2>&1
0 16 * * 1-5 cd /home/lenovo-home/microbot && source .venv/bin/activate && python -m microbot.trail >> /home/lenovo-home/microbot/trail.log 2>&1
```

**Important:** `SHELL=/bin/bash` is required — cron defaults to `/bin/sh` (dash on Ubuntu) which does not support `source`. Without it, both jobs silently fail at the activate step and never run.

Both crons do `git pull` before running. The engine needs it to pick up the latest `morning_verdicts.json`; the intraday cron needs it so code changes pushed after 9:35 AM the previous day are picked up before the scanner runs (the intraday cron runs one minute before the engine cron, so without its own pull it would always lag a full day behind).

Logs: `engine.log` and `intraday.log` in the repo root.

**Note:** The CCR swing engine routine (`trig_01S594UwnSLYX9HNtNZmeXgG`) is **disabled** — it could never connect to Alpaca from the sandbox. The local cron is the sole execution path.

## Diagnostics layer

Every execution routine runs a health check before doing real work:

- **`python -m microbot.premarket_check`** — full pre-market audit (7:30 AM routine). Checks credentials, Alpaca connection, account equity, every open position's active stop order, verdicts freshness, DB integrity, git repo, and core module imports. Prints a GO/NO-GO verdict.
- **`python -m microbot.diagnostics`** — lightweight pre-engine check. Same critical checks, skips the position stop audit and git check. Exits non-zero on failure. Run locally before the engine if needed; **not embedded in any CCR routine** (CCR sandbox blocks Alpaca, causing false failures).

Both can be run locally at any time.

### Daily diagnostics report (Google Drive)

After the 7:30 AM pre-market check runs, the routine saves a Google Doc to Drive named:

```
microbot Pre-Market Diagnostics - YYYY-MM-DD [GO]
microbot Pre-Market Diagnostics - YYYY-MM-DD [NO-GO]
```

The doc contains the full diagnostics checklist output plus the day's routine schedule. Find it in Google Drive under the account pete.torres.375@gmail.com.

**Known CCR false positives** — the diagnostics container has restricted outbound networking. These two checks always fail in that environment and should be ignored in the Drive report:
- `alpaca_connect` — Host not in allowlist (network sandbox, not a real credential issue)
- `db_missing_tables` — fresh container starts with no DB (trading routines have a persistent local DB)

Focus on: morning verdicts freshness, core module imports, and git repo access — those reflect real system state.

**CCR networking limitations (2026-06-08):** The CCR container cannot connect to Alpaca at all — not just in diagnostics, but in any code. As a result:
- The swing engine CCR routine (`trig_01S594UwnSLYX9HNtNZmeXgG`) is **disabled** — execution runs via local cron instead
- The intraday scanner CCR routine no longer runs diagnostics — it goes straight to the gap scan and web search briefing
- The morning analysis routine uses a GitHub PAT embedded in the push URL to authenticate `git push` (the CCR container has no stored credentials)

## Self-improvement loop (safe version)

The weekly remote optimizer does walk-forward grid search and proposes better strategy parameters. **Nothing is auto-promoted.** Workflow:

1. Optimizer runs every Monday 9am ET (CCR routine above)
2. Pushes `optimizer_proposals.json` to repo if improvements found
3. User: `git pull && python import_proposals.py`
4. User: `python -m microbot.approvals --params` to approve/reject
5. Engine picks up approved params on next run

## Google Sheets dashboard

`run_research.py` pushes three tabs to the sheet at `GSHEET_ID`:

- **Watchlist** — one row per symbol (best strategy), filtered to score > 0 or trades ≥ 3 with positive expectancy, sorted A→Z. Navy timestamp banner + column guide with strategy descriptions.
- **LiveSignals** — signals that fired today (entry, stop, target, reason). Populates when a new signal fires on an unowned symbol.
- **Positions** — current open positions pulled live from Alpaca: symbol, shares, entry, current price, P&L $, P&L %, stop, target, and a **Health** column. Health shows the R-multiple (`(current - entry) / (entry - stop)`) with labels: `+1.2R STRONG` / `+0.3R Winning` / `Breakeven` / `-0.2R At Risk`. Stop/target only populate during market hours when bracket legs are active. After hours, Health falls back to `Winning (no stop)` / `At Risk (no stop)` so the label is never mistaken for a real R-multiple grade. Future improvement: read stop/target from the journal DB instead of live orders for true 24/7 R-multiple.
- **DailyTrades** — today's human-approved trades (status=`submitted` in the journal): symbol, strategy, qty, entry, stop, target, dollar risk, and time approved. Resets each day. Added 2026-06-03 (commit 8e97e54).

`run_research.py` sleeps 20 seconds after writing Watchlist/LiveSignals before pushing Positions and DailyTrades, to avoid hitting the Google Sheets 60-writes/minute quota.

The daily scan routine refreshes the sheet automatically at 9:35 AM ET every trading day.

## Scoring and backtest accuracy

The ranking score is **drawdown-adjusted**: `expectancy_R × √trades × DD_penalty`, where `DD_penalty = 1 / (1 + |max_dd_R| / 8)`. A strategy with a −28R historical drawdown scores ~64% less than an identical one with a −5R drawdown. This prevents deep-drawdown names (GOOG, AMD, GLD) from dominating the watchlist despite high raw expectancy.

The backtester models **overnight gap-fills**: if a bar opens below the stop price, the fill is at the open (not the stop). This produces honest R-multiples for volatile names and feeds accurate drawdown inputs to the score formula.

The engine prints an explicit skip message when even 1 share exceeds the 1% risk budget (e.g. `skip GOOG: 1 share risks $13.75 > $5.00 budget`), so oversized stocks are visibly filtered rather than silently dropped.

## Atomic bracket orders

A bracket order submits three legs in one request: entry, stop-loss, and take-profit. **Atomic** means all three go in together — if the entry fills, the stop and target are guaranteed to exist. There is no risk of ending up in a position with no stop because a separate order failed.

Bracket orders use `TimeInForce.GTC` (not DAY) so stop and take-profit legs survive overnight. A prior bug used DAY, which canceled all legs at market close and left positions unprotected — fixed 2026-06-03 (commit 8506ef4).

## Trailing stops (both layers)

Added 2026-06-12 after UBXG rode a +1.7R open gain back toward its original stop:

- **Intraday (ORB):** the 25%-of-max-gain trail arms once the position is up 1R (reduced from 50% after ICCM 2026-06-17 showed the tighter trail exits too early on volatile gap stocks). The scale-out's breakeven move is clamped so it never lowers an already-trailed stop.
- **Swing (`microbot/trail.py`):** ratchet at each engine run plus a market-hours cron (9:36 AM, 10:30–3:30 ET hourly, 4:00 PM) — any position up ≥ 1R gets its live stop order raised to entry + 50% of the gain. Ratchet-only, never lowered; the 2:1 target leg stays. Original risk comes from the journal's order record, and the journal stop is deliberately not updated so dashboard R-multiples keep the original-risk denominator. Prices are verified against the latest real trade print (size > 0, stamped today) before any math — ghost/reference quotes (SPCX IPO day) are skipped, which also makes holiday runs a clean no-op. Unit tests in `tests/test_trail.py`.

## After-hours order management

Alpaca only allows one sell order per position at a time (shares are "held" for the order). To have both a stop-loss and a take-profit live simultaneously, use **OCO (One Cancels Other)** orders via `LimitOrderRequest` with `order_class=OrderClass.OCO`, `take_profit=TakeProfitRequest(limit_price=...)`, and `stop_loss=StopLossRequest(stop_price=...)`. When one leg fills, the other is automatically canceled.

After market close, bracket legs from a GTC bracket order remain active as linked OCO legs — no manual intervention needed for normally-entered positions. Only manually-placed orders (standalone stops or targets) require OCO replacement.

## Order sizing: whole shares only

The bot uses bracket orders (entry + stop + take-profit in one atomic order). Alpaca bracket orders do not support fractional quantities, so sizing is always rounded down to whole shares. **Do not add fractional share support** — it would require splitting each trade into 3 separate orders, losing atomicity and adding orphaned-stop failure modes. Revisit only if moving to a real ~$500 account where 1 share regularly exceeds the risk budget; in that case, prefer trimming the universe to sub-$50 stocks first.

## Split handling

`splits.py` runs at engine startup, detects recent forward/reverse splits via Alpaca's CorporateActionsClient, and rescales stored stop/target/entry prices in the journal so they stay accurate. Adjustments are idempotent (tracked in `split_adjustments` table).

## Key files

| File | Purpose |
|---|---|
| `microbot/engine.py` | Main run loop — call `run_once()` |
| `microbot/config.py` | All settings, universes, env vars |
| `microbot/strategies.py` | Strategy classes + factories |
| `microbot/screener.py` | Backtest + rank universe |
| `microbot/optimizer.py` | Walk-forward parameter optimizer |
| `microbot/journal.py` | SQLite trade journal |
| `microbot/approvals.py` | Human approval gate |
| `microbot/splits.py` | Corporate action / split handling |
| `microbot/trail.py` | Daily 1R stop ratchet for swing positions |
| `microbot/reconcile.py` | Closes open journal orders by checking Alpaca bracket legs |
| `microbot/diagnostics.py` | Lightweight pre-engine health check (credentials, Alpaca, verdicts, DB, imports) |
| `microbot/premarket_check.py` | Full pre-market audit (adds stop order audit, git check, position R display) |
| `run_optimizer.py` | Run optimizer + write proposals JSON |
| `import_proposals.py` | Import remote proposals into local DB |
| `run_research.py` | Research-only scan (no trades) |

## Morning verdicts integration

The morning CCR analysis routine (10am ET) determines CLEAN/CAUTION/AVOID for each symbol and writes `morning_verdicts.json`. The engine and `morning_review.py` read this file to gate signals:

- **CLEAN** — signal is surfaced in the interactive picker (`morning_review.py`)
- **CAUTION** — queued with `[CAUTION]` prepended to the reason so it's visible in approvals
- **AVOID** — skipped entirely with a printed message
- **No verdict** — treated normally (no change to existing behavior)

File format written by the CCR routine:
```json
{ "date": "2026-06-02", "verdicts": { "BB": "CLEAN", "GLD": "CAUTION", "AMD": "AVOID" } }
```

The file is stale-checked by date — if it's from a previous day, verdicts are ignored.

## Running

```bash
# Research only (safe, no orders)
python run_research.py

# Interactive morning picker — run after the 10am CCR analysis completes
python morning_review.py

# Full run with approval gate
python -m microbot.engine

# Day trading (ORB) — runs scanner then engine, auto-closes at 3:55 PM ET
python run_intraday.py             # scan + trade
python run_intraday.py --scan-only # just print candidates, no trading
python -m microbot.intraday_scanner  # scanner only (writes intraday_candidates.json)

# Review pending trade approvals
python -m microbot.approvals

# Review optimizer proposals
python import_proposals.py
python -m microbot.approvals --params

# Reconcile closed brackets into the journal
python -m microbot.reconcile            # write closed trades
python -m microbot.reconcile --dry-run  # preview without writing

# Rebalance portfolio to a target set of symbols (one command replaces manual closes + buys)
python rebalance.py --target IREN,LEGN,LUNR,TGTX,KEEL   # execute
python rebalance.py --target IREN,LEGN,LUNR,TGTX,KEEL --dry-run  # preview

# Run optimizer manually
python run_optimizer.py
```

## Day trading layer (ORB)

Added 2026-06-04. Fully automated Opening Range Breakout engine that runs alongside the swing bot.

**Risk rules:** 1% equity per trade · max 2 concurrent intraday positions · 2% daily loss limit halts trading · hard EOD close at 3:55 PM ET — never overnight

**Strategy:** 5-minute ORB — entry on break above first 5-min candle high, stop at ORB low, scale out half at 2:1, trail remaining at 25% of max gain above entry

**Automation:** Cron runs `run_intraday.py` at 9:34 AM ET weekdays. Scanner finds gap 5%+ candidates with 2x+ pace-adjusted rel volume (per-minute rate vs. historical average, not raw cumulative). CCR routine (`trig_01TX4CDGSGMLscLLgtkgeKAr`) runs at 9:15 AM ET to print a pre-market news briefing on candidates.

**Performance gate:** After 20+ trades, pull any strategy below 45% win rate. Track results in `intraday_trades` and `intraday_daily` journal tables.

## Rebalance command (`rebalance.py`)

Automates portfolio restructuring in one step. Given a `--target` list:
1. **Closes** any position not in the target (cancels bracket legs first, then submits close)
2. **Scans** for live signals on symbols to buy; if no signal, prompts for stop price
3. **Places** GTC bracket orders for each approved buy
4. **Updates** the journal (marks closed positions, logs manual close records)

Use `--dry-run` to preview without placing orders. Built 2026-06-04 to reduce manual workflow.

## Current focused universe (as of 2026-06-16)

Active portfolio: **BTI, CSCO, GOOG, SPCX**. `MAX_OPEN_POSITIONS=5` — 4/5, 1 slot open. SPCX: re-entered 2026-06-17 at $208.825, 2 shares, stop $190.00 (trailing), no target, long-term hold. Risk/share $18.825, 2-share risk $37.65. Trail cron ratchets once up ≥ 1R.

| Symbol | Entry | Notes |
|---|---|---|
| BTI | $59.82 | British American Tobacco. Dividend momentum. 17 shares. Stop $56.98 (OCO), target $65.52. |
| CSCO | $121.04 | Cisco. EMA pullback. 7 shares. Stop $113.98 (OCO), target $133.13. Slightly offside. |
| GOOG | $365.95 | Google. Trend momentum. 6 shares (duplicate signal fired 2026-06-15). Stop $350.59 (OCO), target $399.12. |

Recently closed: SPCX stopped out 2026-06-16 at $196.30 (+$46.72, +1.02R; IPO re-entry, trail stop hit 3:47 PM ET). GRAB EOD close 2026-06-16 at $3.52 (-$13.15, -0.25R, ORB intraday). F market sell 2026-06-16 at $14.62 (-$29.89, -0.67R, one-shot cron). UBXG stopped out 2026-06-12 at $7.75 (+$36.92, ORB intraday). SPCX target hit 2026-06-12 at $165.64 (+$98.98, SpaceX IPO trade, ~1.87R). GOOG stopped out 2026-06-11 at $344.36 (~-1.0R). TGTX hit target +$88.76 (+1.52R) on 2026-06-04. KEEL stopped out -$52.36 (-1.00R) on 2026-06-04. LEGN manual close $0 on 2026-06-04. IREN stopped out 2026-06-04. LUNR stopped out 2026-06-04. VALE stopped out 2026-06-04 at $15.84.

## Environment variables (.env)

```
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
LIVE_TRADING=false
STARTING_EQUITY=5000
MAX_OPEN_POSITIONS=5
INCLUDE_DIVIDEND_STOCKS=true
INCLUDE_SPLIT_STOCKS=true
GSHEET_ID=...
```
