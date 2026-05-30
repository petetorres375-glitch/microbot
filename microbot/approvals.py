"""
approvals.py
------------
The "ask me first" gate for LIVE trading. The engine queues proposed trades here
instead of executing them. You review and approve/reject — only approved trades
are sent to the broker.

Critically, approval RE-VALIDATES against a live account snapshot at the moment
you approve (buying power, not-already-held), because the account can change
between when the bot proposed the trade and when you get to it. A stale proposal
is rejected rather than blindly submitted.

Usage:
    python -m microbot.approvals          # interactive review of pending trades
    python -m microbot.approvals --list   # just list them
The dashboard also exposes Approve/Reject buttons.
"""
from __future__ import annotations

import argparse
import json

from . import journal
from .broker import Broker
from .risk import SizedTrade
from .strategies import Signal


def pending():
    journal.init()
    return journal.fetch_pending()


def _rebuild_sized(a: dict) -> SizedTrade:
    sig = Signal(a["symbol"], a["strategy"], a["side"], a["entry"], a["stop"],
                 a["target"], 0.0, a["reason"] or "")
    return SizedTrade(sig, int(a["qty"]), a["dollar_risk"], a["notional"])


def approve(approval_id: int, broker: Broker | None = None) -> dict:
    """Re-validate and submit a single approved trade. Returns a result dict."""
    journal.init()
    a = journal.get_approval(approval_id)
    if not a:
        return {"ok": False, "msg": f"approval #{approval_id} not found"}
    if a["status"] != "pending":
        return {"ok": False, "msg": f"#{approval_id} already {a['status']}"}

    broker = broker or Broker()
    # --- re-validate against a live snapshot ---
    acct = broker.account()
    if a["symbol"] in broker.held_symbols():
        journal.set_approval_status(approval_id, "rejected", note="already held")
        return {"ok": False, "msg": f"{a['symbol']} already held — rejected"}
    if a["notional"] > acct["buying_power"]:
        journal.set_approval_status(approval_id, "rejected", note="insufficient buying power")
        return {"ok": False, "msg": f"insufficient buying power for {a['symbol']} — rejected"}

    sized = _rebuild_sized(a)
    try:
        order = broker.submit_bracket(sized)
        journal.set_approval_status(approval_id, "submitted", alpaca_id=str(order.id))
        journal.log_order(sized, str(order.id), str(order.status))
        return {"ok": True, "msg": f"submitted {sized.qty}x {a['symbol']}",
                "alpaca_id": str(order.id)}
    except Exception as e:
        journal.set_approval_status(approval_id, "error", note=str(e))
        return {"ok": False, "msg": f"order failed: {e}"}


def reject(approval_id: int, note: str = "user rejected") -> dict:
    journal.init()
    a = journal.get_approval(approval_id)
    if not a or a["status"] != "pending":
        return {"ok": False, "msg": "not pending"}
    journal.set_approval_status(approval_id, "rejected", note=note)
    return {"ok": True, "msg": f"rejected #{approval_id}"}


def _interactive():
    rows = pending()
    if not rows:
        print("No trades awaiting approval.")
        return
    broker = Broker()
    mode = "LIVE" if not broker.paper else "PAPER"
    print(f"[{mode}] {len(rows)} trade(s) awaiting approval:\n")
    for a in rows:
        print(f"  #{a['id']}  {a['qty']}x {a['symbol']} ({a['strategy']})  "
              f"entry~{a['entry']}  stop {a['stop']}  target {a['target']}  "
              f"risk ${a['dollar_risk']:.2f}  score {a['score']}")
        print(f"       reason: {a['reason']}")
        ans = input(f"       approve #{a['id']}? [y]es / [n]o / [s]kip / [q]uit: ").strip().lower()
        if ans in ("q", "quit"):
            break
        if ans in ("y", "yes"):
            print("      ", approve(a["id"], broker)["msg"])
        elif ans in ("n", "no"):
            print("      ", reject(a["id"])["msg"])
        else:
            print("       skipped (stays pending)")


# ---- parameter proposal review ----

def pending_proposals():
    journal.init()
    return journal.fetch_pending_proposals()


def approve_proposal_params(proposal_id: int) -> dict:
    journal.init()
    ok = journal.approve_param_proposal(proposal_id)
    if ok:
        return {"ok": True, "msg": f"proposal #{proposal_id} approved and promoted to active params"}
    return {"ok": False, "msg": f"proposal #{proposal_id} not found or not pending"}


def reject_proposal_params(proposal_id: int, note: str = "user rejected") -> dict:
    journal.init()
    p = journal.get_proposal(proposal_id)
    if not p or p["status"] != "pending":
        return {"ok": False, "msg": "not pending"}
    journal.reject_param_proposal(proposal_id, note)
    return {"ok": True, "msg": f"rejected #{proposal_id}"}


def _interactive_params():
    rows = pending_proposals()
    if not rows:
        print("No parameter proposals awaiting review.")
        return
    print(f"{len(rows)} parameter proposal(s) awaiting review:\n")
    for p in rows:
        proposed = json.loads(p["proposed_params_json"])
        current = json.loads(p["current_params_json"])
        print(f"  #{p['id']}  {p['strategy']}  +{p['improvement_pct']:.1f}% OOS")
        print(f"       OOS score: {p['oos_score']:.3f}  (current: {p['current_oos_score']:.3f})")
        print(f"       proposed params: {json.dumps(proposed)}")
        print(f"       current  params: {json.dumps(current)}")
        ans = input(f"       approve #{p['id']}? [y]es / [n]o / [s]kip / [q]uit: ").strip().lower()
        if ans in ("q", "quit"):
            break
        if ans in ("y", "yes"):
            print("      ", approve_proposal_params(p["id"])["msg"])
        elif ans in ("n", "no"):
            print("      ", reject_proposal_params(p["id"])["msg"])
        else:
            print("       skipped (stays pending)")


def main():
    p = argparse.ArgumentParser(description="review trades and optimizer proposals")
    p.add_argument("--list", action="store_true", help="list pending trades and exit")
    p.add_argument("--params", action="store_true",
                   help="review pending parameter improvement proposals")
    args = p.parse_args()

    if args.params:
        _interactive_params()
        return
    if args.list:
        for a in pending():
            print(f"#{a['id']}  {a['qty']}x {a['symbol']} ({a['strategy']})  "
                  f"stop {a['stop']} target {a['target']} risk ${a['dollar_risk']:.2f}")
        return
    _interactive()


if __name__ == "__main__":
    main()
