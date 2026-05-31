"""
ipo_scanner.py
--------------
Discovers recently IPO'd US equities by querying SEC EDGAR for recent
8-A12B filings (new exchange registrations on NYSE / NASDAQ). Cross-checks
each discovered ticker against Alpaca to confirm it is active and tradable.
Results are cached in the DB; the EDGAR scan runs at most once every 24 hours
so it doesn't slow down every screener call.

No extra API keys required — EDGAR is free and public.
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import List

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetStatus

from .config import settings
from . import journal


_EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"
_EDGAR_SUBMISSIONS = "https://data.sec.gov/submissions"
# EDGAR requires a descriptive User-Agent with contact info.
_UA = "microbot-ipo-scanner/1.0 (automated research bot; contact: owner)"
_RESCAN_HOURS = 24


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _recent_8a_ciks(days: int) -> List[str]:
    """Return CIK strings for companies that filed 8-A12B in the last `days` days."""
    start = (date.today() - timedelta(days=days)).isoformat()
    end = date.today().isoformat()
    url = (
        f"{_EDGAR_SEARCH}?q=%22%22&forms=8-A12B"
        f"&dateRange=custom&startdt={start}&enddt={end}"
    )
    try:
        data = _get_json(url)
    except Exception as e:
        print(f"  (ipo_scanner) EDGAR search failed: {e}")
        return []
    hits = data.get("hits", {}).get("hits", [])
    return [
        h["_source"]["entity_id"]
        for h in hits
        if h.get("_source", {}).get("entity_id")
    ]


def _cik_to_ticker(cik: str) -> str | None:
    """Return the primary ticker for a CIK, or None if unavailable."""
    padded = str(int(cik)).zfill(10)
    url = f"{_EDGAR_SUBMISSIONS}/CIK{padded}.json"
    try:
        data = _get_json(url)
    except Exception:
        return None
    tickers = data.get("tickers", [])
    return tickers[0].upper() if tickers else None


def _is_tradable(symbol: str) -> bool:
    """Return True if the symbol is active and tradable on Alpaca."""
    try:
        client = TradingClient(settings.api_key, settings.api_secret, paper=True)
        asset = client.get_asset(symbol)
        return bool(asset.tradable and asset.status == AssetStatus.ACTIVE)
    except Exception:
        return False


def discover_ipos() -> List[str]:
    """
    Main entry point. Queries EDGAR for recent IPOs, validates each against
    Alpaca, caches the results, and returns all currently active discovered
    symbols. Skips the EDGAR scan if one ran within the last 24 hours.

    Only returns auto-discovered symbols — the screener merges this with
    the manually-configured settings.ipo_universe.
    """
    last_scan = journal.get_scan_log("ipo_scan")
    if last_scan:
        age_h = (
            datetime.now(timezone.utc) - datetime.fromisoformat(last_scan)
        ).total_seconds() / 3600
        if age_h < _RESCAN_HOURS:
            return journal.fetch_known_ipos()

    print("  (ipo_scanner) scanning EDGAR for recent IPOs...")
    known = set(journal.fetch_known_ipos())
    rejected = set(journal.fetch_rejected_ipos())

    ciks = _recent_8a_ciks(settings.ipo_lookback_days)
    new_count = 0
    for cik in ciks:
        ticker = _cik_to_ticker(cik)
        time.sleep(0.12)  # EDGAR rate limit: stay well under 10 req/s
        if not ticker or ticker in known or ticker in rejected:
            continue
        if _is_tradable(ticker):
            journal.add_discovered_ipo(ticker)
            known.add(ticker)
            new_count += 1
            print(f"  (ipo_scanner) + {ticker} added")
        else:
            journal.reject_discovered_ipo(ticker)
            rejected.add(ticker)

    journal.set_scan_log("ipo_scan")
    label = f"{new_count} new" if new_count else "no new"
    print(f"  (ipo_scanner) {label} IPO(s) found — {len(known)} total in cache")
    return journal.fetch_known_ipos()
