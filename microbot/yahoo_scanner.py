"""
yahoo_scanner.py
----------------
Fetches Yahoo Finance trending tickers and most-active lists, filters out
symbols already in the universe, runs a quick backtest on the rest, and
returns ranked candidates worth considering for the universe.

Usage:
    python -m microbot.yahoo_scanner          # print candidates
    python -m microbot.yahoo_scanner --top 10 # limit output
"""
from __future__ import annotations

import json
import math
import urllib.request
from typing import List, Dict

from .config import settings
from .data import MarketData
from .backtest import backtest_symbol
from .strategies import build_default_strategies, build_dividend_strategies
from . import metrics


_TRENDING_URL = "https://query1.finance.yahoo.com/v1/finance/trending/US?count=25"
_MOST_ACTIVE_URL = (
    "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
    "?formatted=true&scrIds=most_actives&count=25&start=0"
)
_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=_HEADERS)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def _get_trending() -> List[str]:
    try:
        data = _fetch_json(_TRENDING_URL)
        return [q["symbol"] for q in data["finance"]["result"][0]["quotes"]]
    except Exception:
        return []


def _get_most_active() -> List[str]:
    try:
        data = _fetch_json(_MOST_ACTIVE_URL)
        return [q["symbol"] for q in data["finance"]["result"][0]["quotes"]]
    except Exception:
        return []


def _is_valid(symbol: str) -> bool:
    """Filter out crypto, foreign listings, and obvious non-equity symbols."""
    return (
        symbol.isalpha()
        and symbol.isupper()
        and 1 <= len(symbol) <= 5
    )


def _score(exp: float, trades: int, maxdd: float,
           min_t: int = 8, dd_tol: float = 8.0) -> float:
    if trades < min_t:
        return 0.0
    return round(exp * math.sqrt(trades) / (1 + abs(maxdd) / dd_tol), 3)


def _current_universe() -> set:
    universe = set(settings.universe)
    if settings.include_dividend_stocks:
        universe.update(settings.dividend_universe)
    if settings.include_split_stocks:
        universe.update(settings.split_universe)
    return {s.upper() for s in universe}


def fetch_candidates(top: int = 10) -> List[Dict]:
    """
    Return ranked new candidates from Yahoo Finance trending + most active.
    Skips symbols already in the universe.
    """
    trending = _get_trending()
    most_active = _get_most_active()

    # Deduplicate, tag source, filter invalids
    seen: Dict[str, str] = {}
    for sym in trending:
        if _is_valid(sym):
            seen.setdefault(sym, "trending")
    for sym in most_active:
        if _is_valid(sym):
            if sym in seen:
                seen[sym] = "trending+active"
            else:
                seen[sym] = "most_active"

    current = _current_universe()
    new_symbols = [s for s in seen if s not in current]

    if not new_symbols:
        return []

    md = MarketData()
    all_strats = build_default_strategies(rr=settings.reward_risk_ratio) + \
                 build_dividend_strategies(rr=settings.reward_risk_ratio)

    results = []
    for sym in new_symbols:
        try:
            df = md.bars(sym, lookback_days=365)
        except Exception:
            continue
        if df is None or df.empty or len(df) < 60:
            continue

        best = None
        live_signal = None
        for strat in all_strats:
            if len(df) < strat.min_bars():
                continue
            trades = backtest_symbol(strat, sym, df)
            if trades and len(trades) >= 3:
                m = metrics.compute(trades)
                sc = _score(m["expectancy_r"], m["trades"], m["max_drawdown"])
                if best is None or sc > best["score"] or \
                        (sc == best["score"] and m["trades"] > best["trades"]):
                    best = {
                        "strategy": strat.name,
                        "trades": m["trades"],
                        "win_rate": m["win_rate"],
                        "expectancy_r": m["expectancy_r"],
                        "max_dd_r": m["max_drawdown"],
                        "score": sc,
                    }
            sig = strat.evaluate(sym, df)
            if sig and live_signal is None:
                live_signal = sig

        if best is None:
            continue

        price = round(float(df["close"].iloc[-1]), 2)
        results.append({
            "symbol": sym,
            "source": seen[sym],
            "price": price,
            **best,
            "live_signal": live_signal is not None,
            "signal_entry": live_signal.entry if live_signal else None,
            "signal_stop": live_signal.stop if live_signal else None,
            "signal_target": live_signal.target if live_signal else None,
        })

    results.sort(key=lambda r: (r["live_signal"], r["score"], r["expectancy_r"]),
                 reverse=True)
    return results[:top]


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=10)
    args = p.parse_args()

    print("Fetching Yahoo Finance trending + most active...")
    candidates = fetch_candidates(top=args.top)

    if not candidates:
        print("No new candidates found outside current universe.")
        return

    print(f"\n{'Symbol':<8} {'Source':<16} {'Price':>7} {'Strategy':<18} "
          f"{'Trades':>6} {'WR':>5} {'Exp':>6} {'MaxDD':>7} {'Score':>6} {'Signal'}")
    print("-" * 95)
    for r in candidates:
        sig = ">> SIGNAL" if r["live_signal"] else ""
        print(f"{r['symbol']:<8} {r['source']:<16} ${r['price']:>6.2f} "
              f"{r['strategy']:<18} {r['trades']:>6} {r['win_rate']:>4.0%} "
              f"{r['expectancy_r']:>6.3f} {r['max_dd_r']:>7.2f} "
              f"{r['score']:>6.3f} {sig}")
        if r["live_signal"]:
            print(f"         entry={r['signal_entry']} stop={r['signal_stop']} "
                  f"target={r['signal_target']}")

    print(f"\nTo add a symbol: add it to UNIVERSE= in .env")


if __name__ == "__main__":
    main()
