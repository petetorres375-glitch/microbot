# microbot — Claude Code Context

## What this project is

A Python paper-trading bot connected to Alpaca's paper API. Starting equity ~$500 (paper account shows $100k Alpaca default). Swing-trading focus, long-only, no day trading.

## Key design principle

**The bot is a scanner and executor — the user is the decision layer.**

The user's own trade analyzer consistently outperforms the bot's raw signals. The bot's job is to:
- Scan 30+ symbols across multiple strategies 24/7
- Size positions correctly and never forget a stop
- Surface ranked candidates for human review
- Never trade emotionally

The approval gate (`python -m microbot.approvals`) exists precisely because human judgment on *which* setups to take is better than the bot's mathematical threshold. The bot catches things the user would miss; the user filters out things the bot can't see (earnings, news, macro, sector context).

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

## Self-improvement loop (safe version)

The weekly remote optimizer (`run_optimizer.py`) does walk-forward grid search and proposes better strategy parameters. **Nothing is auto-promoted.** Workflow:

1. Scheduler runs every Monday 9am ET (CCR routine `trig_01PYxALzYVnZuA88Kpror5Qo`)
2. Pushes `optimizer_proposals.json` to repo if improvements found
3. User: `git pull && python import_proposals.py`
4. User: `python -m microbot.approvals --params` to approve/reject
5. Engine picks up approved params on next run

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
| `run_optimizer.py` | Run optimizer + write proposals JSON |
| `import_proposals.py` | Import remote proposals into local DB |
| `run_research.py` | Research-only scan (no trades) |

## Running

```bash
# Research only (safe, no orders)
python run_research.py

# Full run with approval gate
python -m microbot.engine

# Review pending trade approvals
python -m microbot.approvals

# Review optimizer proposals
python import_proposals.py
python -m microbot.approvals --params

# Run optimizer manually
python run_optimizer.py
```

## Environment variables (.env)

```
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
LIVE_TRADING=false
STARTING_EQUITY=500
INCLUDE_DIVIDEND_STOCKS=true
INCLUDE_SPLIT_STOCKS=true
GSHEET_ID=...
```
