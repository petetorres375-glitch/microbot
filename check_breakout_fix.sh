#!/bin/bash
# Recurring check for the 2026-08-28 breakout volume-gate fix (commit a1c7b94).
# Runs weekdays via crontab until breakout/breakout_52w shows up as a live
# signal (bullet or veto line) in engine.log — then self-deletes: removes its
# own crontab line and this file. No manual re-scheduling needed.
#
# Known blind spot: the "N Signals Today!" bullets only show the top 3 of
# that day's live signals, and a signal on an already-held symbol is never
# printed at all (silent `if s["symbol"] in held: continue`, see the
# Notify Rankings Bug fix note in CLAUDE.md). A breakout signal hiding in
# either of those gaps won't be caught here — this is a fast heuristic
# check, not a rigorous one.
cd /home/lenovo-home/microbot || exit 1
today=$(date +%Y-%m-%d)
section=$(awk -v d="$today" '$0 ~ d {f=1} f' engine.log)
live_evidence=$(echo "$section" | grep -E "•.*breakout|vetoed by analyzer: breakout")

{
  echo "=== breakout fix check: $(date) ==="
  if [ -n "$live_evidence" ]; then
    echo "FOUND breakout as a live signal today — fix verified, removing self:"
    echo "$live_evidence"
  else
    echo "NOT FOUND — no breakout/breakout_52w live signal or veto line today (still unverified)"
  fi
  echo "--- full live-signal/veto lines from today's run, for context ---"
  echo "$section" | grep -E "live signals today|vetoed by analyzer|Signals Today|•|AUTO\(CLEAN\)"
} >> breakout_fix_check.log 2>&1

if [ -n "$live_evidence" ]; then
  crontab -l | grep -v "check_breakout_fix.sh" | crontab -
  rm -f "$0"
fi
