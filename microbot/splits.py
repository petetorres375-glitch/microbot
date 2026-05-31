"""
splits.py
---------
Detects forward and reverse stock splits via Alpaca's CorporateActionsClient,
then rescales journal entry/stop/target prices and share qty so the dashboard
stays accurate after a split.

Alpaca automatically adjusts open bracket orders for corporate actions, so
only the local journal rows need fixing — the Alpaca-side orders are already
correct.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import List, Tuple

from alpaca.data.historical.corporate_actions import CorporateActionsClient
from alpaca.data.requests import CorporateActionsRequest

from .config import settings
from . import journal


def _ca_client() -> CorporateActionsClient:
    settings.assert_keys()
    return CorporateActionsClient(settings.api_key, settings.api_secret)


def fetch_recent_splits(symbols: List[str],
                        lookback_days: int = 30) -> List[Tuple]:
    """
    Returns (symbol, ratio, ex_date, split_type) for each forward/reverse
    split in the last `lookback_days` days for the given symbols.

    For a forward split:  ratio = new_rate / old_rate  (e.g. 2.0 for 2:1).
    For a reverse split:  ratio = old_rate / new_rate  (e.g. 10.0 for 1:10).
    In both cases ratio > 1; split_type tells you the direction.
    """
    if not symbols:
        return []
    client = _ca_client()
    end = date.today()
    start = end - timedelta(days=lookback_days)
    req = CorporateActionsRequest(
        symbols=symbols,
        types=["forward_split", "reverse_split"],
        start=start,
        end=end,
    )
    try:
        ca_set = client.get_corporate_actions(req)
    except Exception as e:
        print(f"  (splits) corporate actions fetch failed: {e}")
        return []

    results: List[Tuple] = []
    for s in ca_set.data.get("forward_splits", []):
        ratio = (s.new_rate / s.old_rate) if s.old_rate else 1.0
        results.append((s.symbol, ratio, s.ex_date, "forward"))
    for s in ca_set.data.get("reverse_splits", []):
        ratio = (s.old_rate / s.new_rate) if s.new_rate else 1.0
        results.append((s.symbol, ratio, s.ex_date, "reverse"))
    return results


def check_and_apply_splits(symbols: List[str] | None = None,
                           lookback_days: int = 30) -> List[dict]:
    """
    Checks for recent splits across all journaled symbols (or a given list).
    Applies price/qty rescaling to journal rows logged before each split's
    ex_date. Skips splits already recorded in split_adjustments.

    Returns a summary list of what was applied.
    """
    if symbols is None:
        symbols = journal.all_symbols()
    if not symbols:
        return []

    applied = []
    for symbol, ratio, ex_date, split_type in fetch_recent_splits(symbols, lookback_days):
        if journal.split_already_applied(symbol, ex_date):
            continue
        n = journal.apply_split(symbol, ratio, ex_date, split_type)
        journal.mark_split_applied(symbol, ratio, ex_date, split_type)
        tag = f"{ratio:.4g}:1 forward" if split_type == "forward" else f"1:{ratio:.4g} reverse"
        applied.append({
            "symbol": symbol, "split": tag,
            "ex_date": str(ex_date), "rows_updated": n,
        })
        print(f"  (splits) {symbol} {tag} (ex {ex_date}) — {n} journal rows rescaled")

    return applied
