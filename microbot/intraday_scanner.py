"""
intraday_scanner.py
-------------------
Pre-market gap scanner for the day trading engine.

Scans a broad watchlist for stocks gapping up >= 5% with elevated
relative volume. Applies morning verdicts (CLEAN/CAUTION/AVOID) as
a news filter, then writes intraday_candidates.json for the engine.

Run standalone:
    python -m microbot.intraday_scanner
    python -m microbot.intraday_scanner --top 5
"""
from __future__ import annotations

import datetime
import json
from datetime import date
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import yfinance as yf
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockSnapshotRequest

from .config import settings
from .yahoo_scanner import _get_most_active, _get_trending, _is_valid

# Curated list of stocks that frequently appear as day trading candidates:
# volatile small/mid caps, Bitcoin miners, EV names, meme stocks, etc.
INTRADAY_UNIVERSE = [
    "MARA", "RIOT", "CLSK", "COIN", "HOOD", "SOFI", "RIVN",
    "GME", "AMC", "BBAI", "SOUN", "SPCE", "IONQ", "RKLB", "JOBY",
    "ACHR", "ASTS", "LUNR", "IREN", "HIMS", "CLOV",
    "LAZR", "LIDR", "OUST", "AEVA", "PRCT", "RXST",
    "WOLF", "BLNK", "CHPT", "NKLA", "MVST", "BFLY",
    "OPEN", "OPRA", "BARK", "XPEV", "LI", "NIO",
    "QURE",
    "SPY", "QQQ", "DIA",
]

MIN_GAP_PCT = 0.05
MIN_REL_VOL = 2.0
MIN_PRICE = 5.0
CANDIDATES_FILE = "intraday_candidates.json"


def _load_verdicts() -> Dict[str, str]:
    try:
        with open("morning_verdicts.json") as f:
            data = json.load(f)
        if data.get("date") == date.today().isoformat():
            return data.get("verdicts", {})
    except Exception:
        pass
    return {}


def _batch_snapshots(symbols: List[str]) -> Dict:
    client = StockHistoricalDataClient(settings.api_key, settings.api_secret)
    # Alpaca accepts up to 100 symbols per request
    result = {}
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        try:
            # Default SIP feed: snapshots are allowed on the free plan (only
            # recent historical bars are restricted), and daily_bar.volume must
            # be consolidated volume — IEX-only volume reads ~50x low against
            # the consolidated average used in _rel_volume.
            snaps = client.get_stock_snapshot(
                StockSnapshotRequest(symbol_or_symbols=chunk)
            )
            result.update(snaps)
        except Exception:
            pass
    return result


def _rel_volume(symbol: str, _current_volume: float) -> float:
    """Pace-adjusted relative volume: today's per-minute rate vs. historical average.

    Both volumes come from yfinance (consolidated) to avoid the ~50x undercount
    from Alpaca's IEX-only daily_bar.volume vs. yfinance's consolidated average.
    """
    try:
        now_et = datetime.datetime.now(ZoneInfo("America/New_York"))
        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        elapsed_minutes = max(1.0, (now_et - market_open).total_seconds() / 60)
        tk = yf.Ticker(symbol)
        avg = tk.fast_info.three_month_average_volume
        if not avg or avg <= 0:
            return 0.0
        hist = tk.history(period="1d", interval="1m")
        today_vol = float(hist["Volume"].sum()) if not hist.empty else 0.0
        if today_vol <= 0:
            return 0.0
        per_min_today = today_vol / elapsed_minutes
        per_min_avg = avg / 390
        return round(per_min_today / per_min_avg, 2)
    except Exception:
        pass
    return 0.0


def scan(top: int = 5, min_gap: float = MIN_GAP_PCT,
         min_relvol: float = MIN_REL_VOL,
         min_price: float = MIN_PRICE) -> List[Dict]:
    """
    Find today's top day trading candidates.

    Returns list of dicts sorted by gap_pct * rel_volume descending.
    Also writes intraday_candidates.json for the engine to consume.
    """
    # Build universe: curated + existing swing + split + Yahoo movers
    universe: set[str] = set(INTRADAY_UNIVERSE)
    universe.update(s.upper() for s in settings.universe)
    if settings.include_split_stocks:
        universe.update(s.upper() for s in settings.split_universe)
    for sym in _get_most_active() + _get_trending():
        if _is_valid(sym):
            universe.add(sym)

    print(f"Scanning {len(universe)} symbols for gaps...")
    snaps = _batch_snapshots(sorted(universe))
    verdicts = _load_verdicts()

    gap_candidates = []
    for sym, snap in snaps.items():
        if snap.previous_daily_bar is None:
            continue
        prev_close = float(snap.previous_daily_bar.close)
        if prev_close <= 0:
            continue

        if snap.latest_trade:
            price = float(snap.latest_trade.price)
        elif snap.minute_bar:
            price = float(snap.minute_bar.close)
        else:
            continue

        if price < min_price:
            continue

        gap_pct = (price - prev_close) / prev_close
        if gap_pct < min_gap:
            continue

        verdict = verdicts.get(sym, "")
        if verdict == "AVOID":
            print(f"  skip {sym}: AVOID verdict")
            continue

        daily_vol = float(snap.daily_bar.volume) if snap.daily_bar else 0.0

        gap_candidates.append({
            "symbol": sym,
            "price": round(price, 2),
            "prev_close": round(prev_close, 2),
            "gap_pct": round(gap_pct, 4),
            "daily_volume": daily_vol,
            "rel_volume": 0.0,
            "verdict": verdict or "NONE",
        })

    print(f"Found {len(gap_candidates)} symbols gapping >= {min_gap:.0%}. "
          f"Checking relative volume...")

    # Fetch rel_volume only for gap candidates (small set = fast)
    for c in gap_candidates:
        c["rel_volume"] = _rel_volume(c["symbol"], c["daily_volume"])

    # Filter by relative volume
    filtered = [c for c in gap_candidates if c["rel_volume"] >= min_relvol]

    # Sort by gap * rel_volume — strongest combined signal first
    filtered.sort(key=lambda c: c["gap_pct"] * c["rel_volume"], reverse=True)
    result = filtered[:top]

    # Write candidates file for engine
    out = {"date": date.today().isoformat(), "candidates": result}
    with open(CANDIDATES_FILE, "w") as f:
        json.dump(out, f, indent=2)

    return result


def main():
    import argparse
    p = argparse.ArgumentParser(description="Pre-market gap scanner")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--min-gap", type=float, default=MIN_GAP_PCT,
                   help="Minimum gap %% (default: 0.05 = 5%%)")
    p.add_argument("--min-relvol", type=float, default=MIN_REL_VOL,
                   help="Minimum relative volume (default: 2.0)")
    p.add_argument("--min-price", type=float, default=MIN_PRICE,
                   help="Minimum price filter (default: 5.0)")
    args = p.parse_args()

    candidates = scan(top=args.top, min_gap=args.min_gap, min_relvol=args.min_relvol,
                      min_price=args.min_price)

    if not candidates:
        print("No qualifying candidates today.")
        return

    print(f"\n{'Symbol':<8} {'Gap%':>6} {'Price':>7} {'RelVol':>7} {'Verdict':<8}")
    print("-" * 45)
    for c in candidates:
        print(f"{c['symbol']:<8} {c['gap_pct']:>5.1%} {c['price']:>7.2f} "
              f"{c['rel_volume']:>7.1f}x {c['verdict']:<8}")

    print(f"\nCandidates written to {CANDIDATES_FILE}")


if __name__ == "__main__":
    main()
