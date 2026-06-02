"""
engine.py
---------
The conductor. One `run_once()` does a full cycle:
  1. Connect to Alpaca (paper unless LIVE_TRADING=true, with a confirmation gate).
  2. Read account (equity, buying power, PDT status).
  3. Research the universe and get today's live signals (the "autoresearch").
  4. For each signal: size it, run risk gates, skip symbols we already hold,
     submit a bracket order, and journal everything.
  5. Optionally push the watchlist to Google Sheets.

Run it once a day after the close for swing trading, or on a schedule for
intraday. Use --research-only to scan without trading.

>>> THIS PLACES REAL (paper) ORDERS. Read the README first. <<<
"""
from __future__ import annotations

import argparse

from .broker import Broker
from .config import settings
from . import journal
from . import feedback
from . import notify
from . import splits
from .risk import RiskConfig, size_trade
from .screener import research
from .strategies import Signal, build_strategies_from_params
from . import tracker_gsheets


# ---- YOUR trade analyzer is wired in via feedback.py ---------------------
# feedback.compute_vetoes() reuses your analyzer's calculate_expectancy /
# calculate_win_rate on the bot's CLOSED trades to find losing setup/symbol
# combos (once they have a real sample), and vetoes them here. Add any extra
# manual rules below.
def apply_trade_analyzer(signal_row: dict, vetoes: dict) -> bool:
    """Return True to ALLOW the trade, False to VETO it."""
    if not feedback.allow_signal(signal_row, vetoes):
        return False
    # --- add your own manual rules here if you like, e.g. ---
    # if signal_row["symbol"] in {"SOME", "BLOCK", "LIST"}: return False
    return True
# --------------------------------------------------------------------------


def _confirm_live():
    if settings.live_trading:
        gate = ("Trades will be QUEUED for your per-trade approval."
                if settings.require_live_approval
                else "WARNING: auto-execution is ON — no per-trade approval!")
        print("\n*** LIVE TRADING IS ENABLED — REAL MONEY ***")
        print(f"    {gate}")
        ans = input("Type 'I UNDERSTAND' to proceed: ").strip()
        if ans != "I UNDERSTAND":
            raise SystemExit("Aborted. Set LIVE_TRADING=false to paper trade.")


def run_once(research_only: bool = False, push_sheets: bool = False):
    journal.init()
    _confirm_live()

    # Detect any splits that occurred since the last run and rescale journal
    # entries so stored stops/targets stay consistent with Alpaca's adjusted prices.
    applied = splits.check_and_apply_splits()
    if applied:
        notify.notify(f"microbot: split adjustment applied for "
                      f"{', '.join(a['symbol'] for a in applied)}")

    broker = Broker()
    acct = broker.account()
    mode = "LIVE" if not broker.paper else "PAPER"
    print(f"[{mode}] equity=${acct['equity']:.2f} buying_power=${acct['buying_power']:.2f} "
          f"daytrades={acct['daytrade_count']} PDT={acct['pattern_day_trader']}")

    if acct["pattern_day_trader"] and acct["equity"] < 25000:
        print("  ! PDT flag set & equity < $25k — day trades are restricted. "
              "Favor swing (overnight) holds.")

    # Load any approved parameter improvements from the DB.
    active_params = journal.fetch_active_params()
    if active_params:
        print(f"  using promoted params for: {', '.join(active_params)}")
    default_strats = build_strategies_from_params(active_params, rr=settings.reward_risk_ratio)
    div_strats = build_strategies_from_params(active_params, rr=settings.reward_risk_ratio,
                                              dividend=True)

    print("\nResearching universe (backtest + rank)...")
    result = research(default_strats=default_strats, div_strats=div_strats)
    print(f"  top candidates: " + ", ".join(
        f"{r['symbol']}/{r['strategy']}({r['score']})"
        for r in result["rankings"][:5] if r["score"] > 0) or "  (none scored > 0)")
    print(f"  live signals today: {len(result['live_signals'])}")

    if push_sheets:
        tracker_gsheets.push_research(result)

    if research_only:
        return result

    # ---- act on live signals ----
    cfg = RiskConfig(
        risk_per_trade_pct=settings.risk_per_trade_pct,
        max_open_positions=settings.max_open_positions,
    )
    held = broker.held_symbols()
    open_positions = len(held)
    open_risk = 0.0  # (simplified; real open risk would sum live stops)
    # In paper mode use STARTING_EQUITY so sizing reflects the real account
    # size we're simulating, not Alpaca's default $100k paper balance.
    equity = settings.starting_equity if broker.paper else acct["equity"]
    bp = min(acct["buying_power"], equity)

    # Adaptive feedback from YOUR analyzer over the bot's closed trades.
    vetoes = feedback.compute_vetoes()
    if vetoes["combos"] or vetoes["setups"]:
        print(f"  analyzer vetoes -> setups:{vetoes['setups'] or '∅'} "
              f"combos:{len(vetoes['combos'])}")

    # "Ask me first": live queues for approval; paper auto-executes (unless you
    # opt to practice approvals on paper).
    require_approval = (settings.require_live_approval if not broker.paper
                        else settings.paper_require_approval)
    queued = 0

    for s in result["live_signals"]:
        if s["symbol"] in held:
            continue
        if not apply_trade_analyzer(s, vetoes):
            print(f"  vetoed by analyzer: {s['strategy']}/{s['symbol']}")
            continue

        risk_per_share = s["entry"] - s["stop"]
        risk_budget = equity * cfg.risk_per_trade_pct
        if risk_per_share > risk_budget:
            print(f"  skip {s['symbol']}: 1 share risks ${risk_per_share:.2f}"
                  f" > ${risk_budget:.2f} budget (stock too expensive to size)")
            continue

        sig = Signal(s["symbol"], s["strategy"], "buy", s["entry"], s["stop"],
                     s["target"], 0.0, s["reason"], settings.reward_risk_ratio)
        journal.log_signal(sig, taken=False)

        sized = size_trade(sig, equity, bp, cfg, open_risk, open_positions)
        if sized is None:
            print(f"  skip {s['symbol']}: fails risk/sizing gate")
            continue

        # Tentatively reserve so we don't propose more than the account allows.
        open_positions += 1
        open_risk += sized.dollar_risk
        bp -= sized.notional

        if require_approval:
            aid = journal.enqueue_approval(sized, score=s.get("score", 0.0))
            queued += 1
            print(f"  QUEUED #{aid}: {sized.qty}x {s['symbol']} stop {s['stop']} "
                  f"target {s['target']} (risk ${sized.dollar_risk:.2f}) — awaiting approval")
        else:
            try:
                order = broker.submit_bracket(sized)
                journal.log_order(sized, str(order.id), str(order.status))
                print(f"  ORDER {sized.qty}x {s['symbol']} @~{s['entry']} "
                      f"stop {s['stop']} target {s['target']} (risk ${sized.dollar_risk:.2f})")
            except Exception as e:
                print(f"  order failed for {s['symbol']}: {e}")

    if queued:
        notify.notify(f"microbot has {queued} trade(s) awaiting your approval. "
                      f"Review: `python -m microbot.approvals` or the dashboard.")

    notify.notify_summary(
        signals=len(result["live_signals"]),
        top_candidates=result["rankings"][:3],
        equity=acct["equity"],
    )

    return result


def main():
    p = argparse.ArgumentParser(description="microbot trading engine")
    p.add_argument("--research-only", action="store_true",
                   help="scan + rank without placing any orders")
    p.add_argument("--push-sheets", action="store_true",
                   help="push watchlist/signals to Google Sheets")
    p.add_argument("--flatten", action="store_true",
                   help="EMERGENCY: cancel orders and close all positions")
    args = p.parse_args()

    if args.flatten:
        journal.init()
        b = Broker()
        print("Flattening everything...")
        b.close_all()
        return

    run_once(research_only=args.research_only, push_sheets=args.push_sheets)


if __name__ == "__main__":
    main()
