#!/usr/bin/env python3
"""
morning_review.py
-----------------
Interactive morning picker. Run after the 10am CCR analysis completes.

  python morning_review.py

Shows each live signal with its CLEAN/CAUTION/AVOID verdict and lets you
choose which ones to queue for approval. AVOID signals are skipped automatically.
"""
import sys
sys.path.insert(0, ".")

from microbot import verdicts as verdicts_mod, journal, feedback
from microbot.broker import Broker
from microbot.config import settings
from microbot.risk import RiskConfig, size_trade
from microbot.screener import research
from microbot.strategies import Signal, build_strategies_from_params


def main():
    journal.init()
    verdicts = verdicts_mod.load()

    if not verdicts:
        print("  ! No verdicts file found for today — showing all signals without filtering.")
        print("    (The 10am CCR analysis routine writes morning_verdicts.json)")
    else:
        cleans   = [s for s, v in verdicts.items() if v == "CLEAN"]
        cautions = [s for s, v in verdicts.items() if v == "CAUTION"]
        avoids   = [s for s, v in verdicts.items() if v == "AVOID"]
        print(f"Morning verdicts loaded — CLEAN: {cleans or '∅'}  "
              f"CAUTION: {cautions or '∅'}  AVOID: {avoids or '∅'}\n")

    broker = Broker()
    acct = broker.account()
    equity = settings.starting_equity if broker.paper else acct["equity"]
    bp = min(acct["buying_power"], equity)
    held = broker.held_symbols()

    active_params = journal.fetch_active_params()
    default_strats = build_strategies_from_params(active_params, rr=settings.reward_risk_ratio)
    div_strats     = build_strategies_from_params(active_params, rr=settings.reward_risk_ratio,
                                                  dividend=True)

    print("Scanning universe for live signals...\n")
    result = research(default_strats=default_strats, div_strats=div_strats)
    signals = result["live_signals"]

    if not signals:
        print("No live signals today.")
        return

    cfg = RiskConfig(
        risk_per_trade_pct=settings.risk_per_trade_pct,
        max_open_positions=settings.max_open_positions,
    )
    vetoes = feedback.compute_vetoes()
    open_positions = len(held)
    open_risk = 0.0
    queued = 0

    for s in signals:
        if s["symbol"] in held:
            print(f"  {s['symbol']:6s} — already holding, skipped")
            continue

        verdict = verdicts.get(s["symbol"], "")

        if verdict == "AVOID":
            print(f"  {s['symbol']:6s} — AVOID (morning analysis) skipped\n")
            continue

        # Show signal details
        tag = f"[{verdict}]" if verdict else "[--]"
        print(f"  {tag} {s['symbol']}  {s['strategy']}  score={s['score']:.3f}")
        print(f"       entry ${s['entry']:.2f}  stop ${s['stop']:.2f}  "
              f"target ${s['target']:.2f}  |  {s.get('reason', '')}")

        risk_per_share = s["entry"] - s["stop"]
        risk_budget = equity * cfg.risk_per_trade_pct
        if risk_per_share > risk_budget:
            print(f"       skip: 1 share risks ${risk_per_share:.2f} > ${risk_budget:.2f} budget\n")
            continue

        sig = Signal(s["symbol"], s["strategy"], "buy", s["entry"], s["stop"],
                     s["target"], 0.0,
                     f"{'[CAUTION] ' if verdict == 'CAUTION' else ''}{s.get('reason', '')}",
                     settings.reward_risk_ratio)
        sized = size_trade(sig, equity, bp, cfg, open_risk, open_positions)
        if sized is None:
            print(f"       skip: fails risk/sizing gate\n")
            continue

        ans = input(f"       Queue {sized.qty}x {s['symbol']} "
                    f"(risk ${sized.dollar_risk:.2f})? [y/n]: ").strip().lower()
        print()

        if ans == "y":
            aid = journal.enqueue_approval(sized, score=s.get("score", 0.0))
            open_positions += 1
            open_risk += sized.dollar_risk
            bp -= sized.notional
            queued += 1
            print(f"       Queued #{aid} ✓\n")
        else:
            print(f"       Skipped.\n")

    if queued:
        print(f"{queued} trade(s) queued. Run `python -m microbot.approvals` to approve.")
    else:
        print("Nothing queued.")


if __name__ == "__main__":
    main()
