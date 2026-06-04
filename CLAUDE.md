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

## Scheduled CCR routines

| Routine | ID | Schedule | Purpose |
|---|---|---|---|
| Morning signal analysis | `trig_019TFaNMJyiH1atY2kykNHGD` | Weekdays 10:00 AM ET | Web-searches news on universe, delivers CLEAN/CAUTION/AVOID verdicts |
| Daily research scan | `trig_019qsZJECstukLDhqDFXcv6R` | Weekdays 9:35 AM ET | Runs `run_research.py`, pushes ranked candidates + live signals to Google Sheets |
| Weekly optimizer | `trig_01PYxALzYVnZuA88Kpror5Qo` | Mondays 9:00 AM ET | Walk-forward grid search, pushes `optimizer_proposals.json` to repo if improvements found |
| Intraday pre-market scanner | `trig_01TX4CDGSGMLscLLgtkgeKAr` | Weekdays 9:15 AM ET | Runs gap scanner, web-searches news on candidates, prints CLEAN/MIXED/AVOID DAY briefing |

View routine results at: https://claude.ai/code/routines

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
| `microbot/reconcile.py` | Closes open journal orders by checking Alpaca bracket legs |
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

**Strategy:** 5-minute ORB — entry on break above first 5-min candle high, stop at ORB low, scale out half at 2:1, trail remaining at 50% of max gain above entry

**Automation:** Cron runs `run_intraday.py` at 9:20 AM ET weekdays. Scanner finds gap 5%+ candidates with 2x+ rel volume. CCR routine (`trig_01TX4CDGSGMLscLLgtkgeKAr`) runs at 9:15 AM ET to print a pre-market news briefing on candidates.

**Performance gate:** After 20+ trades, pull any strategy below 45% win rate. Track results in `intraday_trades` and `intraday_daily` journal tables.

## Rebalance command (`rebalance.py`)

Automates portfolio restructuring in one step. Given a `--target` list:
1. **Closes** any position not in the target (cancels bracket legs first, then submits close)
2. **Scans** for live signals on symbols to buy; if no signal, prompts for stop price
3. **Places** GTC bracket orders for each approved buy
4. **Updates** the journal (marks closed positions, logs manual close records)

Use `--dry-run` to preview without placing orders. Built 2026-06-04 to reduce manual workflow.

## Current focused universe (as of 2026-06-04)

Active portfolio: **IREN, LEGN, LUNR, TGTX, KEEL**. VALE stopped out 2026-06-04 at $15.84. `MAX_OPEN_POSITIONS=5`.

| Symbol | Entry | Notes |
|---|---|---|
| IREN | $69.80 | AI cloud infra (ex-Iris Energy). $3.65B Microsoft-backed GPU financing closed June 1; 800MW Australian data center announced June 3. Canaccord PT $79. Thesis intact despite -12% drawdown — broad sector rotation, not IREN-specific. |
| LEGN | $37.12 | Legend Biotech. CARVYKTI Q1 sales $597M (+62% YoY). June 2: +42% on LB2501 in vivo CAR-T hitting 100% ORR in B-cell lymphoma (no lymphodepleting chemo). EHA oral presentation June 11-14 (next catalyst). HC Wainwright PT $65. |
| LUNR | $32.40 | Intuitive Machines. $180.4M NASA South Pole contract (IM-5), $20M lunar imaging contracts, Lanteris acquisition. +200% over 6 months. Headwind: $500M ATM offering announced June 3 (dilutive); director sold $5.5M June 1. |
| TGTX | $41.66 | TG Therapeutics. CARVYKTI rival — BRIUMVI for MS. June 3: positive Phase 1 subcutaneous BRIUMVI data (+10% on day). CEO fireside chat Goldman Sachs Healthcare Conference June 9 2pm ET. Phase 3 top-line data expected year-end 2026/early 2027. |
| KEEL | $5.77 | Keel Infrastructure (ex-Bitfarms). Rebranded April 2026, pivoting Bitcoin mining → HPC/AI data centers. Sites: Panther Creek, Sharon, Moses Lake. Goal: 3 leases signed by year-end 2026. Pre-revenue; net loss -$145M last quarter. High-risk story stock. |

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
