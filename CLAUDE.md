# microbot — Claude Code Context

## What this project is

A Python paper-trading bot connected to Alpaca's paper API. Starting equity ~$500 (paper account shows $100k Alpaca default). Swing-trading focus, long-only, with a fully automated intraday ORB layer.

**Note (2026-06-17):** Alpaca has eliminated the Pattern Day Trader (PDT) rule and replaced it with an intraday margin framework. There is no longer a $25,000 minimum equity requirement for unlimited day trading. Guardrails are now margin-based (intraday buying power), not trade-count based. The deprecated API fields `pattern_day_trader`, `daytrade_count`, `last_daytrade_count`, `daytrading_buying_power`, `last_daytrading_buying_power` are removed as of July 6, 2026 — use `buying_power` instead. These fields do not appear in this codebase.

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
- **Dividend universe** (`DIVIDEND_UNIVERSE`): income-focused, lower-beta names — trimmed 2026-06-26 to 5 backtest-positive names only: **KMI, BTI, ET, MO, EPD**. Dropped VZ, AGNC, NLY, STAG (negative expectancy), CVX (flat), ABBV (−42R max drawdown crushes score), O (< 8 backtest trades). Set via `.env` override; toggle with `INCLUDE_DIVIDEND_STOCKS`.
- **Split universe** (`SPLIT_UNIVERSE`): post-split momentum names now affordable (NVDA, TSLA, AMZN, SHOP) — toggle with `INCLUDE_SPLIT_STOCKS`. GOOG removed 2026-08-17, see note below.
- **IPO universe** (`IPO_UNIVERSE`): recent IPOs with limited history, scanned with a shorter 180-day lookback — toggle with `INCLUDE_IPO_STOCKS`, tune lookback with `IPO_LOOKBACK_DAYS`. Auto-discovered via SEC EDGAR 8-A12B filings + Alpaca validation; cached in DB, rescanned every 24h. Manually add extra tickers via `IPO_UNIVERSE=`. Current manual addition: **SPCX** (SpaceX, IPO 2026-06-12). SPCX is also a personal long-term hold — not a bot swing trade. Stop triggered 2026-07-01 at $155.44, filled at $157.62 (entry $163.62, 6 shares, −$36.01). `watch_spcx.py` (cron, same cadence as `trail.py`) watches for a reclaim of $163.62 and re-buys automatically, sized off `starting_equity` with a 5% OCO stop — see "SPCX Long-Term Hold" section below.

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

**Signal quality filters (added 2026-06-21):**
- **Weekly trend filter**: all strategies except `rsi2_reversion` check that the daily bars' weekly close is above a rising 10-week EMA before firing. `rsi2_reversion` uses its own 200-day SMA filter instead. Falls back to "pass" when insufficient weekly history. Controlled via `weekly_filter=True/False` per strategy instance.
- **MeanReversion higher-low gate**: entry bar's low must exceed the prior bar's low, filtering falling-knife entries where the dip is still continuing lower.
- **Sector correlation cap**: engine skips new signals when `MAX_SAME_SECTOR` (default 2, env-var configurable) positions in the same sector are already held. 17 sectors mapped in `config.sector_map`. Symbols not in the map are uncapped.

## Scheduled CCR routines

| Routine | ID | Schedule | Purpose |
|---|---|---|---|
| Pre-Market Diagnostics | `trig_01RGqaa5TuyTVHn2ThGDmxSg` | Weekdays 7:30 AM ET | Full system check: credentials, Alpaca, open position stop audit, DB, git, core imports. GO/NO-GO verdict with ~2 hours to fix before trading starts |
| Morning signal analysis | `trig_019TFaNMJyiH1atY2kykNHGD` | Weekdays 8:30 AM ET | Web-searches news on universe, delivers CLEAN/CAUTION/AVOID verdicts, writes `morning_verdicts_ccr.json` to Google Drive folder (GitHub push blocked by CCR proxy) |
| Intraday pre-market scanner | `trig_01TX4CDGSGMLscLLgtkgeKAr` | Weekdays 9:15 AM ET | Runs gap scanner, web-searches news on candidates, prints CLEAN/MIXED/AVOID DAY briefing |
| Daily research scan | `trig_019qsZJECstukLDhqDFXcv6R` | Weekdays 9:35 AM ET | Runs `run_research.py`, pushes ranked candidates + live signals to Google Sheets. **Non-functional from CCR (confirmed 2026-07-06)** — see networking limitations below |
| Weekly optimizer | `trig_01PYxALzYVnZuA88Kpror5Qo` | Mondays 9:00 AM ET | Walk-forward grid search, pushes `optimizer_proposals.json` to repo if improvements found. **Non-functional from CCR (confirmed 2026-07-06)** — see networking limitations below |

View routine results at: https://claude.ai/code/routines

## Local cron jobs (execution layer)

CCR routines handle analysis and research. **Execution (actual order placement) runs locally** — the CCR container's network sandbox blocks outbound connections to Alpaca, so the engine must run on the local machine where Alpaca API is reachable.

```
# crontab -l
SHELL=/bin/bash
50 8 * * 1-5 cd /home/lenovo-home/microbot && source .venv/bin/activate && python -u fetch_verdicts.py >> /home/lenovo-home/microbot/verdicts.log 2>&1
35 9 * * 1-5 cd /home/lenovo-home/microbot && git pull --quiet && source .venv/bin/activate && python -u -m microbot.engine >> /home/lenovo-home/microbot/engine.log 2>&1
34 9 * * 1-5 cd /home/lenovo-home/microbot && git pull --quiet && source .venv/bin/activate && python -u run_intraday.py >> /home/lenovo-home/microbot/intraday.log 2>&1
36 9 * * 1-5 cd /home/lenovo-home/microbot && source .venv/bin/activate && python -u -m microbot.trail >> /home/lenovo-home/microbot/trail.log 2>&1
30 10-15 * * 1-5 cd /home/lenovo-home/microbot && source .venv/bin/activate && python -u -m microbot.trail >> /home/lenovo-home/microbot/trail.log 2>&1
0 16 * * 1-5 cd /home/lenovo-home/microbot && source .venv/bin/activate && python -u -m microbot.trail >> /home/lenovo-home/microbot/trail.log 2>&1
36 9 * * 1-5 cd /home/lenovo-home/microbot && source .venv/bin/activate && python -u watch_spcx.py >> /home/lenovo-home/microbot/watch_spcx.log 2>&1
30 10-15 * * 1-5 cd /home/lenovo-home/microbot && source .venv/bin/activate && python -u watch_spcx.py >> /home/lenovo-home/microbot/watch_spcx.log 2>&1
0 16 * * 1-5 cd /home/lenovo-home/microbot && source .venv/bin/activate && python -u watch_spcx.py >> /home/lenovo-home/microbot/watch_spcx.log 2>&1
41 9 * * 1-5 cd /home/lenovo-home/microbot && git pull --quiet && source .venv/bin/activate && python -u run_research.py >> /home/lenovo-home/microbot/research.log 2>&1
0 6 * * 1 cd /home/lenovo-home/microbot && git pull --quiet && source .venv/bin/activate && python -u run_optimizer.py >> /home/lenovo-home/microbot/optimizer.log 2>&1
*/10 9-16 * * 1-5 cd /home/lenovo-home/microbot && source .venv/bin/activate && python -u check_positions.py >> /home/lenovo-home/microbot/positions.log 2>&1
15 9 * * 1-5 cd /home/lenovo-home/microbot && git pull --quiet && source .venv/bin/activate && python -u -m microbot.intraday_scanner >> /home/lenovo-home/microbot/intraday_scanner.log 2>&1
36 9 * * 1-5 cd /home/lenovo-home/microbot && source .venv/bin/activate && python -u -m microbot.reconcile >> /home/lenovo-home/microbot/reconcile.log 2>&1
30 10-15 * * 1-5 cd /home/lenovo-home/microbot && source .venv/bin/activate && python -u -m microbot.reconcile >> /home/lenovo-home/microbot/reconcile.log 2>&1
0 16 * * 1-5 cd /home/lenovo-home/microbot && source .venv/bin/activate && python -u -m microbot.reconcile >> /home/lenovo-home/microbot/reconcile.log 2>&1
```

**`microbot.reconcile` added to crontab 2026-07-24** (same cadence as `trail.py`: 9:36 AM, hourly 10:30–3:30, 4:00 PM ET weekdays, logged to `reconcile.log`). Previously manual-only — HOOD and BTI were both found stopped-out on Alpaca but still listed as open in `journal.fetch_open_orders()` since nothing had run reconcile since they closed (BTI closed 2026-07-23, HOOD closed 2026-07-24 9:37 AM ET). Ran manually to catch up (realized $-91.51 combined), then added the cron so this can't silently drift again.

**All cron jobs run with `python -u` (unbuffered stdout), added 2026-07-16.** Without `-u`, Python fully block-buffers stdout when it's redirected to a file (as every cron log line here is) instead of a terminal — a healthy, actively-running process can go an hour+ without a single byte hitting the log, since nothing flushes until the buffer fills or the process exits. This looks identical to a genuine hang from the outside. It caused a real false alarm the same day: `run_intraday.py` (started 9:34 AM) had written nothing to `intraday.log` by 11:00 AM, was killed as a suspected hang, and turned out to have been idling normally in its 30s poll loop past the 10:00 AM `ENTRY_CUTOFF` with no breakout candidates — confirmed via Alpaca's own order history (zero NXTC/OPRA/AEHR orders that day) that nothing was actually lost by the kill, but the diagnosis itself was wrong and the buffered log lines from that session were never flushed, so that session's blow-by-blow output is gone. `-u` makes every print land in the log in real time, so a stale log now reliably means "actually stuck," not "buffered." **Crontab isn't version-controlled** (see note below), so this change only lives in `crontab -l` on this machine — if the crontab is ever rebuilt from scratch, re-add `-u` to every `python` invocation or this exact false-hang trap recurs.

`run_research.py` (added 2026-07-06) and `run_optimizer.py` (added 2026-07-06) run locally because their CCR routine equivalents are non-functional — `data.alpaca.markets` is blocked from the CCR sandbox, so neither can fetch bars there (see "CCR networking limitations" below). `run_research.py` runs weekdays at 9:41 AM ET (after the 9:34/9:35/9:36 engine/intraday/trail crons, to avoid API contention) and pushes the ranked Watchlist/LiveSignals/Positions/DailyTrades tabs to Google Sheets directly — no git push needed. `run_optimizer.py` runs Mondays at 6:00 AM ET, before market-hours crons start, and only writes `optimizer_proposals.json` locally; nothing auto-imports it — still run `python import_proposals.py && python -m microbot.approvals --params` manually per the self-improvement loop workflow below.

`check_positions.py` (added 2026-07-06) is a read-only snapshot of every open position's live P/L, reported against `settings.starting_equity` (not the inflated $100K paper balance). Runs every 10 minutes, 9 AM–4 PM ET weekdays, logged to `positions.log`. Added because a cloud routine can't substitute for this — cloud routines have a 1-hour minimum interval and, like the research scan and optimizer, can't reach Alpaca from the CCR sandbox at all.

**Important:** `SHELL=/bin/bash` is required — cron defaults to `/bin/sh` (dash on Ubuntu) which does not support `source`. Without it, both jobs silently fail at the activate step and never run.

The `fetch_verdicts.py` cron at 8:50 AM bridges the CCR verdicts to git: the 8:30 AM CCR routine writes `morning_verdicts_ccr.json` to a Google Drive folder (GitHub push is blocked from the CCR container), and this script reads it via the service account, writes `morning_verdicts.json`, and commits + pushes so the 9:35 AM engine picks up fresh verdicts. Drive folder: `12_v9m-kyzN4KrUMCXdObQlTEkUBqM7OP` (owned by pete.torres.375@gmail.com, shared with `sheets-bot@sheets-automation-495422.iam.gserviceaccount.com`). Falls back to Google Sheet "Verdicts" tab if Drive file not found. Log: `verdicts.log`.

`watch_spcx.py` (added 2026-07-01, same cadence as `trail.py` above) watches for SPCX to reclaim a trigger price and re-buys — see "SPCX Long-Term Hold" section below. Log: `watch_spcx.log`.

**`15 9 * * 1-5 ... python -m microbot.intraday_scanner`** (first fired 2026-07-10, discovered undocumented 2026-07-15) — a local weekday 9:15 AM cron running the standalone scanner CLI, in parallel with the CCR "Intraday pre-market scanner" routine at the same time. It's not tracked in git (crontab isn't version-controlled), so no commit documents when it was added — timing points to the 2026-07-09 crontab pause/resume during the PENG stuck-order incident, since its first actual run was the next trading day (Fri 2026-07-10 09:15:01 ET, confirmed via syslog; the Jul 9 restore happened at ~9:45 AM, after that day's 9:15 slot had already passed). Originally redundant — `run_intraday.py` (9:34 AM) used to re-scan from scratch and overwrite `intraday_candidates.json` itself. **As of the 2026-07-15 entry-delay fix below, it's load-bearing**: `run_intraday.py` now reuses this cron's output instead of re-scanning, so this is the entry point that actually produces the day's candidate list.

Both engine crons do `git pull` before running. The engine needs it to pick up the latest `morning_verdicts.json`; the intraday cron needs it so code changes pushed after 9:35 AM the previous day are picked up before the scanner runs (the intraday cron runs one minute before the engine cron, so without its own pull it would always lag a full day behind).

Logs: `engine.log`, `intraday.log`, `verdicts.log` in the repo root.

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

**Confirmed 2026-07-06: the block covers market data too, not just trading.** Both the Daily research scan and Weekly optimizer CCR routines fail outright — `data.alpaca.markets` returns 403 from the egress proxy for every symbol's bar fetch (40/40 requests failed on the optimizer run). Only GitHub, Google APIs, and PyPI are reachable from CCR. Practical effect:
- **Daily research scan**: can init the DB, run `reconcile` (no-op), and read that day's `morning_verdicts.json`, but cannot fetch bars, rank candidates, or refresh the Watchlist/LiveSignals Google Sheets tabs. Those only update from a local `run_research.py` run.
- **Weekly optimizer**: produces zero proposals every run — `run_optimizer.py` needs bars for its walk-forward grid search and gets none. `optimizer_proposals.json` will never appear via this routine.
- Both routines need to run **locally** (`python run_research.py` / `python run_optimizer.py`) to actually do their job. Neither is currently on the local crontab — add one if the Sheets dashboard or the self-improvement loop need to stay current without a manual run.

## Self-improvement loop (safe version)

The weekly remote optimizer does walk-forward grid search and proposes better strategy parameters. **Nothing is auto-promoted.** Workflow:

1. Optimizer runs every Monday 9am ET (CCR routine above)
2. Pushes `optimizer_proposals.json` to repo if improvements found
3. User: `git pull && python import_proposals.py`
4. User: `python -m microbot.approvals --params` to approve/reject
5. Engine picks up approved params on next run

## Signal notification accuracy fix (2026-08-06, commit `c739fd8`)

`notify_summary()`'s "N Signals Today!" pop-up/log bullet list was built from `result["rankings"][:3]` (top backtest-score performers) even though the header count came from `result["live_signals"]`. `engine.py`'s separate `top candidates:` print line uses the same rankings list — it shows the top 5 symbols by historical backtest score, **regardless of whether they have an active signal today**. A strong all-time performer (e.g. NOK, scores 2.559/2.226) shows up in both of these every day it backtests well, whether or not it fired.

Surfaced 2026-08-06: NOK appeared in that morning's "4 Signals Today!" bullet list, looked like a dropped trade, but a live `research()` re-run confirmed NOK had **zero live signals** that morning — it never entered `engine.py`'s signal-execution loop (`run_once()` lines ~159-203) at all, so nothing was skipped or lost. Root cause confirmed by process of elimination: every other branch in that loop (sector cap, analyzer veto, verdict gate, sizing gate, order failure) prints a message on skip; the *only* silent branch is `if s["symbol"] in held: continue`. Since NOK produced zero output of any kind, it was never a real candidate — just backtest-ranking decoration.

Fixed: `notify_summary()` now takes separate `live_signals` and `rankings` params — the "N Signals Today" bullets use `live_signals[:3]` (what actually fired), and `rankings[:3]` is only used for the "no signals today, top picks were..." fallback message. `engine.py`'s `top candidates:` print was relabeled `top backtest-ranked (not necessarily live today):` so it can't be misread as live signals again. The real signals table in the journal DB (`journal.log_signal`) also isn't reliable for reconstructing which symbols fired on a given past day — it's only called *after* the held/sector/analyzer/verdict gates, so days where every signal lands on an already-held or vetoed symbol log nothing (this table hasn't gained a new row since mid-June for exactly that reason) — not fixed as part of this change, just noted.

## Breakout volume-gate drought fixed 2026-08-28 (commit pending)

`breakout` (and `breakout_52w`) produced **zero live signals for 6+ weeks** despite scoring well in backtests and despite an undocumented optimizer approval on 2026-08-03 that quietly changed `channel` 25→15 (making the setup *easier* to hit, not harder — this approval was never recorded here, unlike the 7/13 one). Investigation triggered by the user pushing back on ongoing losses and asking to dig into why `breakout` had gone quiet.

Root cause: `Breakout.evaluate()` and `Breakout52w.evaluate()` gate on same-day volume confirmation — `df["volume"].iloc[-1] >= vol_mult * 20-day avg`. The swing engine runs at **9:34 AM ET, ~4 minutes into the 390-minute session**. Alpaca's `"1Day"` bar for the current trading day updates live and only reflects trades since the open up to the fetch time — so `volume.iloc[-1]` at 9:34 AM is a tiny fraction of a full day's volume, compared against a *full-day* 20-day average. This comparison fails almost every single run regardless of what the stock does by end of day. `trend_momentum`/`dividend_momentum`/`rsi2_reversion` have no same-day volume gate, so they were unaffected — which is exactly why only `breakout`-family strategies went dark while everything else kept firing normally.

Confirmed by replaying `backtest_symbol()` against real historical bars, truncated to each date as it would have looked live: `breakout` cleanly fired (positive score, `sig=True`) on WBD 8/18, HOOD 8/21, GLD 8/24, AEHR 8/14, LUNR 8/14, PFE 8/6 — real, profitable-looking setups that never appeared anywhere in `engine.log` (no order, no analyzer-veto line, no bullet) because the live 9:34 AM run's `vol_ok` check failed on the partial-day bar every time, so `screener._scan_symbols()` never even added them to `live_signals` in the first place — not an execution-loop bug, a signal-generation bug.

Fixed: added `indicators.pace_adjusted_volume(df, now=None)` — projects the latest bar's volume to a full-session equivalent when that bar is dated today and the market is still open (`vol * 390 / elapsed_minutes`), the same pace-adjustment `intraday_scanner.py` already uses for ORB relative-volume. A completed historical bar (any backtest day, or today evaluated after the close) is returned unchanged, so backtest scoring and rankings are provably untouched — the adjustment only ever fires when `df.index[-1].date() == now.date()` during the 9:30–4:00 window, which is never true inside `backtest_symbol()`'s walk-forward loop. Both `Breakout.evaluate()` and `Breakout52w.evaluate()` now call this instead of reading `df["volume"].iloc[-1]` directly. New tests in `tests/test_pace_adjusted_volume.py`; full suite (49 tests) passes.

**Not yet verified live** — the fix takes effect on the next trading day's 9:34/9:35 AM cron; watch for `breakout` finally appearing in `engine.log`'s live-signal bullets or veto lines instead of going silently dark.

## Google Sheets dashboard

`run_research.py` pushes three tabs to the sheet at `GSHEET_ID`:

- **Watchlist** — one row per symbol (best strategy), filtered to score > 0 or trades ≥ 3 with positive expectancy, sorted A→Z. Navy timestamp banner + column guide with strategy descriptions.
- **LiveSignals** — signals that fired today (entry, stop, target, reason). Populates when a new signal fires on an unowned symbol.
- **Positions** — current open positions pulled live from Alpaca: symbol, shares, entry, current price, P&L $, P&L %, stop, target, and a **Health** column. Health shows the R-multiple (`(current - entry) / (entry - stop)`) with labels: `+1.2R STRONG` / `+0.3R Winning` / `Breakeven` / `-0.2R At Risk`. Stop/target only populate during market hours when bracket legs are active. After hours, Health falls back to `Winning (no stop)` / `At Risk (no stop)` so the label is never mistaken for a real R-multiple grade. Future improvement: read stop/target from the journal DB instead of live orders for true 24/7 R-multiple.
- **DailyTrades** — today's submitted orders (journal `orders` table, all sources — CLEAN auto-executes from the engine as well as manual/rebalance orders): symbol, strategy, qty, entry, stop, target, dollar risk, and submission time. Resets each day. Added 2026-06-03 (commit 8e97e54). **Fixed 2026-07-21:** originally read the `approvals` table filtered to `status='submitted'`, which only the manual `python -m microbot.approvals` gate ever populated — since the engine's CLEAN verdicts auto-execute straight to `journal.log_order()` without touching `approvals` (see "Key design principle" above), the tab had silently pushed 0 rows on every fully-automated day since approvals stopped being part of the normal flow. `push_daily_trades()` now reads `fetch_orders()` filtered to today's `ts` instead.

**Fixed 2026-07-30 (commit `be52f5e`), superseding the 2026-07-07 fix below:** the 30s-sleep + 1-retry approach never actually worked in practice — `research.log` showed the Positions tab hitting the write-quota 429 on 14 of the last 16 runs (~88%). Root cause: each tab push was issuing ~20-25 separate Sheets API write calls — `_format_header_cells` alone looped one `ws.format()` call per column (8-9 calls just for header colors), plus more individual calls for merges, freeze, guide sections, and row banding. Watchlist+LiveSignals together burned most of the 60-writes/minute quota before Positions/DailyTrades even started, and no sleep/retry duration was going to fix a call-volume problem. `tracker_gsheets._push_tab()` now builds every format/merge/freeze/dimension operation as a raw `batchUpdate` request and issues exactly **2 API calls per tab** (1 values write, 1 formatting batch) — a full 4-tab research run now uses ~8 total write calls instead of ~90+. Verified live against the real sheet with zero 429s. `run_research.py`'s fixed sleep was trimmed 30s→5s accordingly (quota headroom is no longer the bottleneck), and the retry safety net bumped to 2 retries/30s wait as defense in depth.

Superseded — kept for history: `run_research.py` used to sleep 30 seconds after writing Watchlist/LiveSignals before pushing Positions and DailyTrades, to avoid hitting the Google Sheets 60-writes/minute quota. **2026-07-07 (commit a11812b):** the fixed pause wasn't always enough — Watchlist/LiveSignals alone could consume most of the quota, causing Positions/DailyTrades to silently skip on a 429. `push_positions()`/`push_daily_trades()` retried each Sheets write once (with a 20s wait) on a 429 before giving up. This turned out to be insufficient (see the 2026-07-30 fix above).

The daily scan routine refreshes the sheet automatically at 9:35 AM ET every trading day.

## Scoring and backtest accuracy

The ranking score is **drawdown-adjusted**: `expectancy_R × √trades × DD_penalty`, where `DD_penalty = 1 / (1 + |max_dd_R| / 8)`. A strategy with a −28R historical drawdown scores ~64% less than an identical one with a −5R drawdown. This prevents deep-drawdown names (GOOG, AMD, GLD) from dominating the watchlist despite high raw expectancy.

The backtester models **overnight gap-fills**: if a bar opens below the stop price, the fill is at the open (not the stop). This produces honest R-multiples for volatile names and feeds accurate drawdown inputs to the score formula.

The engine prints an explicit skip message when even 1 share exceeds the 1% risk budget (e.g. `skip GOOG: 1 share risks $13.75 > $5.00 budget`), so oversized stocks are visibly filtered rather than silently dropped.

**Backtest performance fixed 2026-07-14 (commits `ba294b4`, `b79dc49`):** `backtest_symbol()`'s bar-by-bar walk-forward loop was recomputing every indicator (EMA, ADX, ATR, RSI, SMA, Bollinger, Donchian, volume avg, weekly trend filter) from scratch on a growing `df.iloc[:i+1]` window at **every single bar** — O(n²) per (symbol, strategy). One symbol/strategy pair over 755 bars took 6.7s; across the ~45-symbol universe × 7 strategies that was the entire 10-11 minute gap between the 9:35 AM engine cron firing and trades actually executing (2026-07-14: RLAY/HOOD didn't place until ~9:49 AM). Every `Strategy` now has a `precompute(df)` that computes each indicator ONCE on the full history (all are causal rolling/EWM computations, so a value at date d is identical whether computed on the full df or any prefix window ending at or after d — no lookahead introduced); `evaluate()` takes an optional `cache` and looks values up by date instead of recomputing. The weekly trend filter (`ind.weekly_ema_aligned`) was the single biggest remaining cost — it called `resample("W-FRI")` fresh on every bar. Its per-bar check ("close above a rising partial-week EMA") algebraically collapses to one condition — close above the EMA through the end of the last **fully completed** week — so `ind.weekly_ema_aligned_series()` computes it with one resample + one EWM over the ~150 completed weekly closes instead of 563 resamples over growing daily windows. Verified zero mismatches against the original per-bar code across 49 (symbol, strategy) combinations (14.5x speedup) and 10 symbols' worth of weekly-filter checks (~40x speedup on that piece) before wiring in; full pytest suite (41 tests) still passes. Real research() wall-clock: ~10-11 min → ~4 min (remaining time is now serial per-symbol network bar fetches plus Alpaca API congestion at market open, not CPU-bound backtest work — a further win would be batching `MarketData.bars()` into one multi-symbol request, not yet done).

Same day, fixed a related display bug in `premarket_check.py`: the position R-multiple audit used the **live** stop price (which `trail.py` ratchets upward on winners) as the risk denominator instead of the original stop from the journal. Once a ratcheted stop moves above entry to lock in profit, `(entry − stop)` goes negative and flips the sign of an otherwise-positive R (ET showed `R=-1.65` while up 3.0% and fully protected). `trail.py` deliberately never updates the journal's stop for exactly this reason; `premarket_check.py` just wasn't reading it. Fixed to pull the original stop via `journal.fetch_open_orders()` for the R math while still displaying the live stop price for the "is there an active stop" check.

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
| `microbot/performance.py` | Closed-trade performance summary — runs automatically at end of each engine run, output in `engine.log` |
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

**Automation:** Cron runs `run_intraday.py` at 9:34 AM ET weekdays. Scanner finds gap 5%+ candidates with 2x+ pace-adjusted rel volume (per-minute rate vs. historical average, not raw cumulative) and float ≤ `MAX_FLOAT_M` shares (default 100M as of 2026-06-25, raised from 20M which was too restrictive — filtered all candidates on most days). Float data from yfinance `floatShares`; symbols with no float data pass through. Set `MAX_FLOAT_M=0` in `.env` to disable. CCR routine (`trig_01TX4CDGSGMLscLLgtkgeKAr`) runs at 9:15 AM ET to print a pre-market news briefing on candidates.

**Intraday engine fixes (2026-06-25):** ORB entries now use bracket orders (atomic entry + stop + target in one request) instead of a separate stop placed after fill. Alpaca's wash-trade guard rejected the separate stop with "use complex orders" — bracket orders are immune. A lockfile (`intraday.lock`) prevents simultaneous engine instances; duplicate runs were causing double/triple position sizing (WAVE 2026-06-25: 333 shares from 3 concurrent instances instead of 111).

**Intraday engine fix (2026-06-26, commit c6b0cbf):** `_check_stop_fills()` was using `get_orders(status="open")` to detect whether a bracket stop leg was still active. But bracket/OCO stop legs are HELD — not OPEN — so they were never found. Every poll iteration concluded the stop had already filled and called `_finalize()`, falsely marking the position as closed mid-session. Today this caused FCEL and SDOT to be logged as -1R stop-outs when neither had actually stopped. FCEL was left as an open Alpaca position with no protection after DAY bracket legs expired at close. Fix: fetch each stop leg directly by ID and check its actual status (HELD/NEW → still active; FILLED → real stop; CANCELED/EXPIRED → orphaned, clear ID without finalizing). Also: `_close_position()` no longer calls `_finalize()` when the market close order fails, preventing the engine from treating a position as closed when it never actually sold.

**Intraday scanner CLI float-cap fix (2026-07-07, commit 9b8b2f9):** `intraday_scanner.py`'s CLI (`python -m microbot.intraday_scanner`) had a hardcoded `--max-float` default of 20M left over from before the 2026-06-25 raise to 100M — it silently overrode `settings.max_float_m` on every standalone run, filtering out valid candidates (e.g. AMPG at 22.2M float, gap +14-16%, rel-vol ~40x on 2026-07-07). The automated 9:34 AM `run_intraday.py` cron was unaffected since it calls `scan()` directly without passing `max_float_m`. Fixed by defaulting the CLI flag to `None` so it falls through to `settings.max_float_m`; removed the dead module constant.

**Intraday engine fix (2026-07-09, commit 38109f5):** `_cancel_stop()` cleared `s.stop_order_id` before confirming the cancel actually succeeded. When a cancel didn't confirm within its 10s poll, tracking was lost permanently — `_check_stop_fills()` stopped monitoring the position (empty ID = skipped), and every later scale-out/close attempt no-op'd the cancel (already empty) then failed the sell against shares Alpaca still held under the original stop. PENG hit this 2026-07-08: scale-out at the 2:1 target failed repeatedly for hours, then the 3:55 PM EOD close failed too, leaving PENG open overnight (surfaced the next morning still holding 12 shares, +$199 unrealized, protected only by the stale $70.45 stop — the local crontab had also been paused since 4:47 PM the prior day pending this exact incident). Fix: `_cancel_stop()` now only clears the ID once Alpaca confirms the order is gone (canceled/filled/expired/rejected), and returns `False` on timeout so scale-out/trail/close callers back off and retry next poll instead of losing track. `_eod_close_all()` now retries each stuck symbol up to 5x (3s apart) before declaring an emergency, using the real ~5 minutes before the 4:00 PM hard close. Crontab was manually restored 2026-07-09 after the incident was understood (PENG's underlying bug fixed separately from the resume decision).

**Intraday scanner socket leak fixed 2026-07-14 (commit `7f605c5`):** the 2026-07-10 yfinance-timeout fix (10s daemon-thread watchdog, see "Intraday Yfinance Timeout Fix") stopped the all-day hang but abandoned the thread on timeout without closing its session — each timed-out symbol leaked one socket in `CLOSE-WAIT` for the life of the process, since the abandoned thread (and the session object it held) stayed alive indefinitely. Found by inspecting the live `run_intraday.py` process (`ss -p`) mid-session on 2026-07-14 — 15 stuck connections to Yahoo, one per timed-out float lookup from that morning's startup scan. Fix: `_get_yf_data()` now creates an explicit `yfinance` session per call and force-closes it on timeout (`session.close()` is safe to call from outside the thread mid-request). Verified against a real hang scenario and a live `AAPL` call; full test suite still passes. **The already-running 07-14 process was on the old code and still has its leaked sockets** — the fix only prevents new leaks going forward, starting with tomorrow's 9:34 AM cron.

**Intraday entry-delay fix 2026-07-15 (commit pending):** even with the socket leak fixed, `run_intraday.py`'s 9:34 AM cron was still calling `scan()` fresh every morning — re-running the full yfinance float/rel-vol lookup across the ~100-symbol universe in the hot path, on top of the 9:15 AM CCR/local scanner cron that already does the same scan minutes earlier. On a slow-Yahoo morning this delayed `IntradayEngine.run()` from starting for several minutes. Concretely on 2026-07-15: the 9:34 AM cron didn't finish scanning until ~9:41 AM, and its first real action — entering AEHR — filled at 9:41:20 AM, 6 minutes after the 9:35 AM opening-range close. The ORB range itself was computed correctly (the code fetches the bar stamped exactly 9:30, not "whatever's latest," so a late start doesn't corrupt the range), but the delayed entry meant the engine chased AEHR into an already-extended move (bought $108.4825, down to ~$100.75 minutes later, protected by a stop at $95.51) instead of catching the breakout near the open. Fix: `run_intraday.py` now reuses the 9:15 AM cron's `intraday_candidates.json` if it's dated today, skipping the redundant re-scan entirely; falls back to a fresh `scan()` only if that file is missing/stale, or if run with `--rescan`. The opening range and all entry/stop math still come from live Alpaca bars fetched at run time regardless of candidate source, so a slightly older watchlist doesn't affect trade accuracy — it only affects which symbols are being watched, and the 9:15/9:34 scans are ~19 minutes apart. This also makes the previously-flagged redundant 9:15 AM local `intraday_scanner` cron entry (see "Local cron jobs" above) load-bearing — it's no longer just a duplicate log, it's now the primary candidate source for the 9:34 AM run.

**Intraday close logging fix (2026-07-23, commit `44feaf6`):** found by cross-referencing `intraday.log` against Alpaca's actual order history — the 2026-07-22 session showed `CLOSED PENG: eod_close exit=59.73 pnl=$-1.50 -0.0R` at what the log implied was 3:55 PM, but the real market-sell order filled at $58.86 at 09:47:42 AM, ~6 hours earlier. Two bugs: (1) `_close_position()` computed PnL from the pre-trade quote snapshot passed into it, not the market sell's actual `filled_avg_price` — during a fast move (right after a daily-loss-limit trip) the real fill can differ meaningfully from the quote, understating PENG's real loss (~$-15, -0.29R) by an order of magnitude; that day's logged "$-151.26" total was actually closer to -$164. (2) `_eod_close_all()` is called both for the real 3:55 PM close and for the daily-loss-limit halt, but hardcoded the print `"3:55 PM — closing all intraday positions"` and reason `"eod_close"` regardless of which path triggered it — so a 9:47 AM risk-limit flatten was logged indistinguishably from an actual end-of-day close. Fixed: `_close_position()` now polls `get_order_by_id()` for up to 10s after the market sell for the real fill price (same pattern already used on entry), falling back to the quote only if the fill never comes back; `_eod_close_all()` takes a `reason` param (`"eod_close"` vs `"daily_loss_halt"`) and only prints the 3:55 PM message for a genuine EOD close. Full test suite (41 tests) still passes.

**Zero-width ORB range fix (2026-08-06, commit pending):** `_set_orb()` used to accept a 9:30 opening bar's high/low as the stop level unconditionally. On thin-liquidity opens, that bar can have a single print — `high == low`, zero width — which isn't a real support/resistance level, just wherever that one trade landed. PRCT hit this same-day: `ORB PRCT: high=18.20 low=18.20 range=0.00`, breakout triggered at 18.34, entered 357 sh @ 18.41 with stop=18.20 (risk_per_share only $0.21 since price had already run up by fill time), stopped out almost immediately at 18.19 for -$79.68 (-1.1R). Seen once before (FCUV, no entry that day, so no prior loss). Fix: `_set_orb()` now sets `s.orb_invalid = True` and refuses to establish the range when `high <= low`, so no breakout entry is ever attempted on that symbol for the rest of the session — `_establish_orb()`'s pending list also excludes invalid symbols so it stops re-fetching the same degenerate bar every poll. 3 new unit tests in `tests/test_intraday_orb.py`; full suite (44 tests) still passes.

**ORB R-multiple bug fixed 2026-08-28 (commit `0c8fb72`):** triggered by the user pushing back on "still losing money" and asking to dig into the ORB trail/scale-out numbers — `performance.py` was reporting ORB expectancy as **-0.42R** with a wildly lopsided avg winner/loser (+0.14R / -0.89R), which read as a real trail-cutting-winners-too-early problem. Root cause: `_finalize()` computed `risk = qty_total * (entry_price - s.stop_price)` using the **live, mutated** `s.stop_price` — which `_manage()` ratchets to breakeven on scale-out and further on every 25%-of-gain trail step — instead of `s.initial_risk` (set once at entry, never mutated, already existed in the dataclass for exactly this reason per its own comment). Any winning trade whose trail moved the stop above entry got a negative/zero risk denominator and silently logged `r_multiple=0.0` regardless of real P&L — 22 of 58 historical closed ORB trades were affected, e.g. AEHR 2026-08-04 +$172.22 logged as `0.0R`, PFSA 2026-08-18 +$79.80 logged as `0.0R`. Actual `pnl` dollar values were never wrong (unaffected total P&L confirmed the bug was display/scoring-only, not a real money leak). Fixed by using `s.initial_risk` in the risk calc; backfilled the 22 affected rows in `microbot.db` directly (gitignored, not part of the commit — a timestamped `.bak` copy was made first). **True ORB numbers: 58 trades, 45% WR (right at the pull-gate line, not below it), expectancy -0.08R, avg winner +0.95R vs avg loser -0.91R** — roughly symmetric R:R, not the structural asymmetry the bug implied. Practical takeaway: ORB isn't a "trail exits winners too early" problem after all; it's close to breakeven and the win rate is the thing to watch against the 45% gate going forward. New regression test in `tests/test_intraday_orb.py` (`test_finalize_uses_initial_risk_not_trailed_stop`); full suite (45 tests) passes.

**Performance gate:** After 20+ trades, pull any strategy below 45% win rate. Track results in `intraday_trades` and `intraday_daily` journal tables. As of the 2026-08-28 R-multiple fix, ORB sits at 45.0% (26W/32L) — right at the line, not below it; watch the next several trades to see which side it settles on.

## Rebalance command (`rebalance.py`)

Automates portfolio restructuring in one step. Given a `--target` list:
1. **Closes** any position not in the target (cancels bracket legs first, then submits close)
2. **Scans** for live signals on symbols to buy; if no signal, prompts for stop price
3. **Places** GTC bracket orders for each approved buy
4. **Updates** the journal (marks closed positions, logs manual close records)

Use `--dry-run` to preview without placing orders. Built 2026-06-04 to reduce manual workflow.

## SPCX Long-Term Hold

Personal long-term position, not a bot swing trade — the daily CCR verdicts and intraday moves don't apply to it. History: IPO 2026-06-12, several manual re-entries/stop-outs through June, last entry 2026-06-30 at $163.62 (6 shares, OCO stop $155.44). Stop triggered 2026-07-01 at $155.44, filled at $157.62 (STOP orders convert to market on trigger — price had bounced by execution time) — actual −$36.01, ~−0.72R.

Per user instruction (2026-07-01): watch for SPCX to reclaim the prior entry price and re-buy automatically rather than manually deciding. `watch_spcx.py` runs on the same cron cadence as `trail.py` (9:36 AM, hourly 10:30–3:30, 4:00 PM ET) and, once SPCX prints a verified real trade (size > 0, stamped today) at or above **$163.62**, buys back in — sized off `starting_equity` at 1% risk, 5% GTC stop below fill via OCO bracket, wide take-profit ceiling since `trail.py`'s 1R ratchet is the real exit. Skips if SPCX is already held, so repeated cron runs are idempotent. Log: `watch_spcx.log`.

**Reclaim triggered 2026-07-06** — SPCX printed $165.14 (≥ $163.62), bought 6 shares, filled $164.88. The OCO stop/target submission crashed with `"oco orders must be exit orders"` — Alpaca's position bookkeeping hadn't caught up to the market fill yet, and the script had no retry, leaving the position briefly unprotected until caught manually (stop placed at $156.63, target $214.34). Fixed same day (commit `8ce4f12`): the script now waits for the position to actually appear before submitting the exit order, retries the OCO on `APIError`, and falls back to a plain stop (no take-profit leg) rather than crashing bare if OCO keeps failing.

**Stopped out again same day, 2026-07-06** — the manually-placed $156.63 stop filled at 1:48 PM ET, 6 shares @ $156.67 avg, ~−$49.27 (~−1.0R). Not held as of end of day; `watch_spcx.py` cron resumes watching for a fresh $163.62 reclaim on the next run.

**Reclaim confirmation added 2026-07-09 (commit `f4171d0`):** the $163.62 trigger whipsawed twice on 2026-07-06 (both reclaims above reversed within hours for a quick stop-out loss), showing the level acts as resistance rather than a clean breakout. `watch_spcx.py` now persists a first-seen-at-or-above-trigger timestamp (`spcx_watch_state.json`, gitignored) across cron runs and only buys once price has held at/above $163.62 for `CONFIRM_MINUTES` (30) of real wall-clock time; any dip back below the trigger resets the clock. Given the hourly cron cadence, this means a reclaim must survive at least one more scheduled check before triggering a buy. Trigger price itself (`$163.62`) is unchanged — this only adds a holding-period gate before acting on it.

## Current focused universe (as of 2026-07-14 ~10 AM ET, market open)

**GOOG removed from `SPLIT_UNIVERSE` 2026-08-17** (`config.py` hardcoded default only — no `.env` override existed). Triggered by a user "why isn't this making money" check-in: account-wide realized P&L stood at −$625.06 across 77 closed trades (43% WR, −0.33R blended). Broke it down by symbol/era rather than accepting the blended number at face value — GOOG was the single worst symbol, 0-for-3 real trades, **−$335.74**, and every one of those losses predates both the 2026-06-21 weekly trend filter and the 2026-07-13 `trend_momentum` param retune, so the strategy that lost on it isn't even the strategy running today. Isolating currently-active strategies from already-dead ones (ema_pullback and manual entries, both already paused/stopped) showed the live system is closer to breakeven-to-slightly-positive than the headline number suggests — `dividend_momentum` +$68.87 (80% WR), `rsi2_reversion` +$48.87 (100%, 1 trade), `trend_momentum` since its 7/13 retune −$21.57 over 4 real trades (50% WR, a big improvement over the GOOG-era number), `breakout` hasn't fired live once since its own 7/13 retune so has no post-fix read yet. ORB's per-trade average loss has also shrunk since this month's fixes (−$10.80/trade pre-fix era → −$3.17/trade in the last 15 trades) even though win rate is still stuck at 40%, below the 45% gate from [[project_phase1_gate_checkin]]. No open GOOG position existed at removal time. Revisit only if GOOG's price action changes enough to justify a fresh look — this wasn't a temporary pause like ema_pullback, it's a same-pattern permanent cut as the 2026-07-16 AMD/ALAB removal below.

**`ema_pullback` paused 2026-07-15** via `DISABLED_STRATEGIES=ema_pullback` in `.env` (mechanism: `settings.disabled_strategies`, checked in `build_default_strategies()`/`build_strategies_from_params()` in `microbot/strategies.py`, commit `eaa10bf`). Reason: worst-performing strategy in the journal (14% win rate, −0.80R expectancy, −$277.85 over 7 trades) and the optimizer structurally can't tune it — see "Optimizer params updated 2026-07-13" correction below. Blocks new signals only; any existing positions opened by this strategy are unaffected and still managed normally by `trail.py`. `.env` is gitignored, so this pause is local-machine-only — doesn't need a push to take effect, but won't survive a fresh `.env` setup elsewhere. To resume: remove `ema_pullback` from `DISABLED_STRATEGIES` (or delete the line).

**Portfolio state as of 2026-08-10 ~3:00 PM ET** (supersedes the 2026-07-24 snapshot below — kept for history): 5 of 8 `MAX_OPEN_POSITIONS` slots — **BB, EPD, ET, F, RLAY**, +$10.01 unrealized (0.20% of $5,000 starting equity).

| Symbol | Strategy | Shares | Entry | Live Stop | Target |
|---|---|---|---|---|---|
| BB | rsi2_reversion | 20 | $10.06 | $7.56 | $12.55 |
| EPD | dividend_momentum | 34 | $37.83 | $36.38 | $40.72 |
| ET | dividend_momentum | 76 | $20.23 | $19.58 | $21.54 |
| F | rsi2_reversion | 31 | $14.24 | $12.65 | $15.82 |
| RLAY | trend_momentum | 20 | $18.86 | $16.47 | $23.64 |

All 5 live stops verified directly against Alpaca (HELD legs via parent-order `.legs`, not `get_orders(status=OPEN)` — see the HELD-legs fix note above). None have reached +1R yet, so none are ratcheted above the original journal stop.

CSCO and HPE (both held as of the 2026-08-04 snapshot) have since closed: **CSCO** target hit 2026-08-05 at $122.5375 (4 shares, entry $110.32, +$48.87, +1.04R); **HPE** stopped out 2026-08-06 at $49.70 (8 shares, entry $47.33, +$18.96, +0.42R — a winning stop, not a loss). **F** resolves the "new position not previously tracked in memory" flag from 2026-08-04 — confirmed in the journal as a real rsi2_reversion entry (entry $14.24, stop $12.65, target $15.82).

**`dividend_momentum`'s optimizer `slow` parameter has flip-flopped twice in 3 weeks:** approved `slow=80` on 2026-07-20 (+300.9% OOS), flipped to `slow=100` sometime since (source run not reviewed live in a session), and the 2026-08-10 weekly run proposed reverting to `slow=80` again (+147.3% OOS, score 5.487 vs current 2.218) — approved same day (commit `51bfe1c`). Two reversals on the same parameter in three weeks may mean the OOS window is short enough to be chasing noise on this strategy specifically; worth checking whether it flips a third time next Monday before trusting these swings as a real edge.

**2026-08-10 engine run:** 6 live signals fired (T, RDW, GLD + 3 more, all `trend_momentum`), all vetoed by the analyzer — no new swing trades placed, position count held at 5/8. ORB had 2 trades: MNDY (+$8.75, ~breakeven — trailed stop caught it before the target) and SLN (−$48.64, −0.93R stop-out).

**Portfolio state as of 2026-07-24 ~2:15 PM ET** (superseded by the 2026-08-10 snapshot above — kept for history): **BB, CSCO, EPD, ET, HPE, RLAY** (swing only), +$85.00 unrealized (1.70% of $5,000 starting equity). `MAX_OPEN_POSITIONS=8` — 6/8, 2 slots open.

| Symbol | Entry | Notes |
|---|---|---|
| ET | $19.47 | Energy Transfer. Dividend momentum. 72 shares. Engine auto-entry 2026-07-06 (CLEAN verdict). Trail ratcheted stop $18.76→$19.83 (+1.1R) 2026-07-13. |
| EPD | $37.75 | Enterprise Products Partners. Dividend momentum. 36 shares. Engine auto-entry 2026-07-08. |
| RLAY | $18.86 | Relay Therapeutics. **trend_momentum** (corrected 2026-07-24 — journal DB order row shows `strategy: trend_momentum`; originally misdocumented as ema_pullback since ema_pullback was that day's top-scoring RLAY signal at 3.591, but the engine log shows it was explicitly vetoed by the analyzer and a separate trend_momentum signal executed instead). 20 shares. Engine auto-entry 2026-07-14, only fired because of the universe-sync fix below plus a same-morning manual CLEAN verdict addition. |
| BB | $10.06 | BlackBerry. rsi2_reversion. 20 shares. Engine auto-entry 2026-07-16. 1:1 bracket off 3x ATR (stop $7.56, target $12.55) — down to −0.60R as of 2026-07-24, stop untouched. |
| CSCO | $110.48 | Cisco. rsi2_reversion. 4 shares (re-entry — prior CSCO position stopped out 2026-07-07). |
| HPE | $47.33 | Hewlett Packard Enterprise. trend_momentum. 8 shares. |

**HOOD stopped out 2026-07-24 at 9:37 AM ET** — entry $110.34 fill (bracket recorded $109.52), stop $96.46 filled $96.4567, 3 shares → **−$41.65 (−1.06R)**.

**BTI re-entered 2026-07-21 at $62.22** (18 shares, previously undocumented here), **stopped out 2026-07-23 at $59.45 → −$49.86 (−1.00R)**.

Both were caught stale in the journal on 2026-07-24 — `fetch_open_orders()` still listed them as open (8 symbols) even though both had already closed on Alpaca, because `reconcile.py` isn't on the crontab and hadn't been run manually since they closed. `python -m microbot.reconcile` was run to close them out properly (realized $-91.51 combined); journal now matches Alpaca's live 6 positions exactly. **Takeaway: `reconcile.py` should probably be added to the crontab** (e.g. alongside the hourly `trail.py` runs) so journal drift like this doesn't require a manual catch — not yet done, flagging for next session.

**Universe/verdicts sync bug fixed 2026-07-14 (commit `b00b46a`):** the CCR morning verdicts routine reads `microbot/config.py`'s hardcoded `UNIVERSE` default (its Step 1), but `.env` is gitignored so the routine never saw the real 38-symbol trading universe — only a stale 23-symbol default (with RKLB/AMKR/LION still in it, 18 real symbols missing including RLAY, NOK, BB, HPE, MRVL, USAR, RDW, LUNR, RGTI, IONQ, QBTS, ALAB, QS, BBAI, WDFC, GLD, AEHR, PENG). Fixed by syncing the default to match `.env` exactly — no behavior change locally, but the CCR routine now analyzes the full universe going forward. Same day, manually verdict-checked and added `RLAY: CLEAN` to that morning's `morning_verdicts.json` (commit `0bd9f86`) with minutes to spare before the 9:35 AM cron — engine picked it up and auto-executed.

**Intraday pre-market scanner routine fixed and re-enabled 2026-07-14:** the same duplicate-bug pattern as the universe/verdicts sync fix above — this CCR routine (`trig_01TX4CDGSGMLscLLgtkgeKAr`) had its own hardcoded, separately-stale, truncated `UNIVERSE=` string plus a literal `.env`-writing step that hardcoded the **raw Alpaca API key and secret in plaintext**. That step also had an `IndentationError` (the `f.write(...)` lines were flush-left, not indented under `with open('.env', 'w') as f:`), which would have crashed Step 1 on every run even before the credential issue. Fixed by deleting the credential-writing block entirely — the routine only ever needed market-data reads and never placed orders, and per the CCR networking limitations above, `data.alpaca.markets` is unreachable from the sandbox regardless, so credentials served no function. Steps renumbered (Step 1 is now venv setup, no `.env`/credential step at all). Routine re-enabled (was `enabled: false`); next run weekdays 9:15 AM ET. The Alpaca paper key/secret that was exposed in plaintext was rotated in the Alpaca dashboard 2026-07-16 (`.env` updated to match, connection reverified working same day) — closed out.

**AMD/ALAB removed from `UNIVERSE` 2026-07-16** (both `.env` and `config.py`'s hardcoded default, same file the CCR verdicts routine reads). Confirmed still structurally unsizeable at removal time: AMD $494.78 close / $37.33 ATR(14), ALAB $318.04 / $41.01 — even a tight 2×ATR stop risks $74.66 and $82.01/share against the $50 (1% of $5,000) budget, 50–65% over. Re-checked against 6 weeks of `engine.log` (29 runs since 2026-06-09): AMD was CLEAN on ~13 of ~24 verdict days but **never produced a single live signal** in that window — its strategies simply never fired on it, so it wasn't even reaching the sizing-gate skip in practice, just burning daily CCR web-search verdict cost for nothing. ALAB had almost no verdict history until the 2026-07-14 universe-sync fix added it to real tracking, but the same current ATR math applies. Revisit only if either stock's price/volatility drops sharply enough to bring 2×ATR under $50/share.

**NXTC ORB trade closed 2026-07-14** — merger-arb name (NextCure/Avere Therapeutics all-stock merger + $320M financing; real catalyst, not a data glitch — thin float 2.26M shares, huge $7.07–$12.23 intraday range). Entered 32 sh @ $9.65, stop $8.27; the violently whipsawing thin-float name gapped through the stop trigger on fill, closing @ $7.91 → **-$55.62 (-1.26R)**. Bracket order worked correctly (no stuck orders) — the overshoot past 1R is ordinary stop-to-market slippage on an extreme mover, same pattern as prior gap-through-stop incidents (SPCX, BTI).

**Alpaca paper API reliably congests 9:30–9:50 AM ET** — verification queries hit `request timed out` (code 50410000) repeatedly during today's market open; not a code bug, just retry after the open settles.

**SOFI stopped out 2026-07-13 at 4:02 PM ET** — exit $18.48 (the ratcheted trail stop, not the original $16.11 bracket stop), entry $17.49, 36 shares → **+$35.64 (+0.72R)**. Trail had ratcheted the stop $16.11→$18.48 earlier that day at +1.4R; price pulled back into the close and tagged the trailed level instead of giving back the full gain — confirms the trail ratchet converting a pullback into a locked-in win rather than a round-trip to breakeven/loss.

**AGEN ORB trade closed 2026-07-13** (intraday, not swing) — 53 shares @ $5.88 entry, scaled out half (26 sh) at the 2:1 target $7.90, remainder trailed and stopped at $6.56 → **+$70.85 total** (journal logs +0.0R on the residual leg due to scale-out R-math, but net was a clear winner).

**GOOG and PENG both closed 2026-07-10** (no auto-executed replacements since — the 2026-07-13 engine run's top candidates BB/RLAY/USAR all lacked CLEAN verdicts and were skipped/vetoed):
- **PENG** (the ORB position adopted into the swing journal after the 2026-07-09 stuck-order incident, see below) — stopped out at $77.80, entry $68.70, 12 shares → **+$109.20 (+2.06R)**. Confirms the `trail.py` ratchet worked end-to-end on an adopted ORB position.
- **GOOG** (ema_pullback re-entry from 2026-07-01) — stopped out at $354.14, entry $358.23, 3 shares → **-$12.27 (-0.25R)**.

**Optimizer params updated 2026-07-13:** the Monday 6 AM `run_optimizer.py` cron ran abnormally long (5h20m+, well beyond typical) and was manually killed partway through `dividend_momentum`. Proposals are saved to the DB as each strategy finishes (`journal.save_param_proposal()`), not just at the end, so the 2 already found were not lost and were approved same day:
- `trend_momentum`: fast 20→10, slow 50→40, adx_min 20→15, stop_mult→2.0 (OOS +251.9%)
- `breakout`: channel 20→25, vol_mult 1.3→1.2, stop_mult→2.0 (OOS +66.6%)

`ema_pullback`, `breakout_52w`, and `rsi2_reversion` were never evaluated this run — need next Monday's run (or a manual `python run_optimizer.py`) to get proposals for those. If the optimizer runs long again, check whether `LOOKBACK_DAYS=1100` combined with the full 7-strategy grid is just inherently this slow.

**`breakout` re-tuned again 2026-08-03 (approval not caught here until the 2026-08-28 breakout-drought investigation):** a later weekly optimizer run proposed and approved `channel` 25→15 (`vol_mult`/`stop_mult` unchanged at 1.2/2.0, +19.0% OOS over the then-current channel=25 params) — found via `param_proposals` table id 5, not from any session's notes. This is the same undocumented-approval pattern as `dividend_momentum`'s `slow` flip-flop (see 2026-08-10 entry above) — the weekly optimizer can silently re-tune a strategy between sessions and nothing forces that change into this file. `channel=15` has been the live param since 8/3; it was not the cause of breakout's 6+ week signal drought (see the volume-gate fix above) — a shorter channel makes the setup easier to hit, not harder.

**Correction (2026-07-15):** the above "never evaluated this run" framing was misleading — `ema_pullback`, `breakout_52w`, and `rsi2_reversion` weren't skipped because of the timeout, they were **structurally absent from `PARAM_GRIDS`/`STRATEGY_CLASSES` entirely**; no run, killed early or not, could have reached them. Added `--strategy` (repeatable) to `run_optimizer.py`/`run_optimization()` (commit `9aaf2d1`) to target specific strategies instead of the full grid, and wired `ema_pullback` into the grids (commit `24b7a8e`: tunes `ema1`, `ema2`, `rsi_lo`, `stop_mult`; `ema3` and `rsi_hi` stay at their constructor defaults, same convention already used for `DividendMomentum`'s un-gridded `rsi_max`). Ran it — **found nothing**: all 81 combos scored exactly `0.000` OOS, including the current-params baseline. Root cause: `_universe_score()` only counts a symbol toward the aggregate score if that symbol produced ≥3 trades (and profit_factor ≥1) within the OOS slice alone; `ema_pullback` fires rarely enough (7 trades total across the live account's entire history) that no symbol crosses 3 trades within the shorter OOS window, for any parameter combination — so the optimizer's per-symbol methodology can't get traction on a low-frequency strategy at this universe size, independent of whether the parameters are actually good or bad. `breakout_52w` and `rsi2_reversion` are still not wired in and would likely hit the same wall if added naively (`breakout_52w` needs 200+ bars and is also low-frequency by design; `rsi2_reversion` additionally has a fixed 1:1 bracket the optimizer's generic `rr`-passing logic isn't designed to touch — see the strategies table above). Given the optimizer can't currently validate a parameter change here, `ema_pullback`'s 14%-win-rate problem (see performance report below) needs a different fix than re-tuning — options raised with the user 2026-07-15: pause the strategy, or pool multiple symbols' trades into a single score instead of requiring 3+ per symbol (would need `_universe_score()` changes, not attempted).

**Note for future ORB positions that survive past their entry day:** `run_intraday.py`'s `IntradayEngine.states` dict is rebuilt fresh each run from that day's scanner candidates (see `run()`), not from existing Alpaca positions — so any ORB position still open the next trading day is invisible to the intraday engine going forward. `trail.py` also won't see it unless it has a row in the swing journal's `orders` table. If this happens again, use `journal.log_manual_order(alpaca_id, symbol, qty, entry, stop, target, strategy="orb_manual")` with the *original* ORB entry/stop/target (not the current live trailed stop) to adopt it into `trail.py`'s management, the same way PENG was handled on 2026-07-09 (see prior incident: crontab paused 2026-07-08 4:47 PM ET, restored 2026-07-09 ~9:45 AM ET after commit `38109f5` fixed the underlying stop-tracking bug).

Recently closed: **SPCX stopped out again 2026-07-06 at $156.67** (6 shares, entry $164.88, ~−$49.27, ~−1.0R — reclaim re-entry from earlier the same morning; see "Reclaim triggered 2026-07-06" above). PENG ORB target/trail exit 2026-07-06 at $67.47 (17 shares, entry $66.64, +$14.11). SOXL ORB target/trail exit 2026-07-06 at $203.41 (7 shares, entry $201.26, +$15.00). F stopped out 2026-07-02 at $13.33 (54 shares, entry $14.37, ≈ −$56, ~−1.1R) — verdict had gone CAUTION morning of and it was already approaching its stop; `trail.py`'s ratchet never engaged since it never reached +1R. SPCX stopped out 2026-07-01 — stop triggered at $155.44 but filled at $157.62 (STOP orders convert to market, price bounced before execution); 6 shares, entry $163.62, actual −$36.01, ~−0.72R. FCEL OCO target hit 2026-07-01 at $33.10 (11 shares, entry $29.60, +$38.50, ~+1.3R — ORB re-entry from Jun 30 closed out same-week). BTI OCO stop filled 2026-07-01 at $61.01 (gapped through $61.47 trail stop; entry $59.82, 17 shares, +$20.27 — profitable exit on job-cut/guidance CAUTION news). UMAC ORB +$61.48 (49 shares, scaled out half at $23.24, stopped remainder at $21.74) — **filled 2026-06-30**, not Jul 1 (corrected 2026-07-01 after Alpaca fill timestamps didn't match the earlier note). OUST EOD close 2026-06-29 at $54.10 (9 shares, entry $48.86, +$47, ~+1.0R; ORB hard close 3:55 PM). NOK stopped out 2026-06-29 at $12.25 (35 shares, entry $13.65, ~−$49, ~−1.0R). FCEL overnight OCO target hit 2026-06-29 at $29.43 (15 shares, entry $22.47, +$105, +1.0R; held overnight on 380 MW data center catalyst). CSCO stopped out 2026-06-26 at $114.18 (7 shares, entry $120.36, −$43.25, ~−0.97R). SDOT intraday ORB target hit 2026-06-26 at $16.11 (25 shares, entry $12.24, +$96.69). WAVE stopped out 2026-06-25 at $9.70 (ORB intraday; 333 shares filled due to duplicate engine instances — lockfile + bracket order fix applied same day). OUST stopped out 2026-06-25 at $41.20 (29 shares, ORB intraday, −$58, ~−1.0R). RKLB stopped out 2026-06-24 at $91.56 (3 shares, entry $109.24, −$53.03, ~−1.09R; gapped through stop on broad market selloff). INTC stopped out 2026-06-23 at $131.63 (3 shares, entry $120.70, +$32.79, ~+0.72R). SPCX stopped out 2026-06-22 at $161.61 (2 shares, entry $184.31, −$45.40, ~-1.0R; after-hours). GOOG stopped out 2026-06-22 at $350.59 (6 shares, ~-1.0R). SPCX stopped out 2026-06-16 at $196.30 (+$46.72, +1.02R). GRAB EOD close 2026-06-16 at $3.52 (-$13.15, -0.25R, ORB). F market sell 2026-06-16 at $14.62 (-$29.89, -0.67R). UBXG stopped out 2026-06-12 at $7.75 (+$36.92, ORB). SPCX target hit 2026-06-12 at $165.64 (+$98.98, ~1.87R). GOOG stopped out 2026-06-11 at $344.36 (~-1.0R). TGTX hit target +$88.76 (+1.52R) on 2026-06-04. KEEL stopped out -$52.36 (-1.00R) on 2026-06-04. LEGN manual close $0 on 2026-06-04. IREN stopped out 2026-06-04. LUNR stopped out 2026-06-04. VALE stopped out 2026-06-04 at $15.84.

## Verdicts pipeline Drive bridge bug — FIXED 2026-07-03

The CCR morning routine creates a **new** `morning_verdicts_ccr.json` file in Drive each day using `mcp__claude_ai_Google_Drive__create_file`. From 2026-07-01 to 2026-07-03 the local `fetch_verdicts.py` cron (8:50 AM) could only see the original Jun 25 file — new daily files were invisible to the service account.

**Root cause found 2026-07-03:** the Drive **folder** (`12_v9m-kyzN4KrUMCXdObQlTEkUBqM7OP`) was never actually shared with `sheets-bot@sheets-automation-495422.iam.gserviceaccount.com` — only the single original Jun 25 file had a direct per-file share. Confirmed via `get_file_permissions`: the folder itself listed only the owner (pete.torres.375@gmail.com); the June 25 file additionally listed the service account as reader; every file created afterward inherited nothing because the folder granted nothing.

**Fix applied:** shared the folder itself with the service account (Viewer role) via the Drive UI. New files created inside it now inherit visibility automatically — no CCR routine change, no code change, no new MCP connector needed. Verified end-to-end same day: `fetch_verdicts.py` found the 2026-07-03 file, wrote `morning_verdicts.json`, committed, and pushed without manual intervention (commit `3f981af`).

If verdicts ever go stale again, first check folder-level sharing before assuming the bug recurred — `mcp__claude_ai_Google_Drive__get_file_permissions` on the folder ID should show the service account as a reader.

**Manual bridge (still available as a fallback, no longer expected to be needed):** Open a Claude Code session, use the Drive MCP tool to read the newest `morning_verdicts_ccr.json`, write its content to `morning_verdicts.json`, push to git, then run `python -m microbot.engine` manually.

## Git commit convention

Every commit Claude makes in this repo must carry both trailers:

```
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Co-Authored-By: Pedro Torres <pete.torres.375@gmail.com>
```

(Model name in the first line should match whichever Claude model is actually authoring, e.g. `Claude Opus 4.8` — keep the second line as-is.) Permanent as of 2026-07-01 — applies from here on, no need to ask each time.

## Environment variables (.env)

```
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
LIVE_TRADING=false
STARTING_EQUITY=5000
MAX_OPEN_POSITIONS=8
INCLUDE_DIVIDEND_STOCKS=true
INCLUDE_SPLIT_STOCKS=true
GSHEET_ID=...
```
