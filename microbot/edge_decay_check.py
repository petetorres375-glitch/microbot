"""
edge_decay_check.py
-------------------
Step 2 of the 2026-10-08 backtest-vs-live investigation. READ-ONLY: downloads
bars and reads the journal's active params; never trades or writes.

timing_gap_check.py showed every live strategy losing in the backtest's own
view over the last ~6 months, even though the screener's ~3-year scores rank
them as profitable. This checks whether that's a recent fade: it runs the
normal backtest (same code, same live params, same universe the screener
uses) over the full lookback, then buckets each strategy's trades into
consecutive ~6-month blocks by ENTRY date, newest block = the last
`block_bars` trading days. A steady edge looks similar in every block; a
faded one is positive early and negative lately.

Unlike timing_gap_check, trades here follow backtest.py's no-overlap rule
(one open trade per symbol/strategy at a time), so N is closer to what could
actually have been traded.

Run:  python -m microbot.edge_decay_check [--block-bars 126]
"""
from __future__ import annotations

import argparse
from typing import Dict, List, Tuple

from .rebaseline import strategy_r_stats

BLOCK_BARS = 126  # ~6 months of trading days


def bucket_by_block(trades: List[Dict], total_bars: int,
                    block_bars: int = BLOCK_BARS) -> Dict[int, List[Dict]]:
    """Pure bucketing by entry bar, counted BACKWARD from the last bar so the
    newest block is always a full `block_bars` long:
      block 0 = entered in the last `block_bars` bars, 1 = the block before...
    The oldest block may be partial (and is still reported). No network."""
    out: Dict[int, List[Dict]] = {}
    last = total_bars - 1
    for t in trades:
        k = (last - t["entry_idx"]) // block_bars
        out.setdefault(k, []).append(t)
    return out


def block_label(dates, total_bars: int, k: int, block_bars: int = BLOCK_BARS) -> str:
    """'YYYY-MM-DD..YYYY-MM-DD' entry-date range covered by block k."""
    hi = total_bars - 1 - k * block_bars
    lo = max(0, hi - block_bars + 1)
    return f"{dates[lo]}..{dates[hi]}"


def pool_blocks(per_symbol: List[Tuple[Dict[int, List[Dict]]]]) -> Dict[int, List[Dict]]:
    """Merge per-symbol block dicts into one pooled dict."""
    pooled: Dict[int, List[Dict]] = {}
    for blocks in per_symbol:
        for k, ts in blocks.items():
            pooled.setdefault(k, []).extend(ts)
    return pooled


def summarize(pooled_by_strategy: Dict[str, Dict[int, List[Dict]]]) -> Dict[str, Dict[int, Dict]]:
    return {name: {k: strategy_r_stats(ts) for k, ts in sorted(blocks.items())}
            for name, blocks in pooled_by_strategy.items()}


def print_report(stats: Dict[str, Dict[int, Dict]], labels: Dict[int, str]) -> None:
    ks = sorted({k for s in stats.values() for k in s}, reverse=True)  # oldest first
    print("\nBacktest expectancy by ~6-month block (entry date), oldest -> newest")
    print("Each cell: trades / win% / avg R\n")
    head = f"{'Strategy':<20}" + "".join(f"{labels.get(k, f'block {k}'):>26}" for k in ks)
    print(head)
    print("-" * len(head))
    for name in sorted(stats):
        row = f"{name:<20}"
        for k in ks:
            s = stats[name].get(k)
            if not s or s["trades"] == 0:
                row += f"{'-':>26}"
            else:
                row += f"{s['trades']:>8} / {s['win_rate'] * 100:>4.0f}% / {s['expectancy_r']:>+6.3f}"
        print(row)


def run(block_bars: int = BLOCK_BARS):
    from .backtest import backtest_symbol
    from .config import settings
    from .data import MarketData
    from . import journal
    from .rebaseline import _combined_universe
    from .strategies import build_strategies_from_params
    from .timing_gap_check import ny_dates

    rr = settings.reward_risk_ratio
    active = journal.fetch_active_params()
    default_strats = build_strategies_from_params(active, rr=rr)
    div_strats = build_strategies_from_params(active, rr=rr, dividend=True)
    symbols, dividend_set, ipo_set = _combined_universe()
    md = MarketData()

    per_strat: Dict[str, List[Dict[int, List[Dict]]]] = {}
    labels: Dict[int, str] = {}
    ref_len = 0
    for i, symbol in enumerate(symbols, 1):
        strats = div_strats if symbol in dividend_set else default_strats
        lb = settings.ipo_lookback_days if symbol in ipo_set else None
        try:
            df = md.bars(symbol, lookback_days=lb)
        except Exception as e:
            print(f"  ! {symbol}: data error {e}")
            continue
        if df is None or df.empty or len(df) < 60:
            continue
        dates = ny_dates(df.index)
        n = len(df)
        if n > ref_len:  # label blocks off the longest history seen
            ref_len = n
            for k in range((n - 1) // block_bars + 1):
                labels[k] = block_label(dates, n, k, block_bars)
        counts = []
        for s in strats:
            trades = backtest_symbol(s, symbol, df)
            per_strat.setdefault(s.name, []).append(bucket_by_block(trades, n, block_bars))
            counts.append(f"{s.name}={len(trades)}")
        print(f"  [{i}/{len(symbols)}] {symbol}: " + ", ".join(counts))

    pooled = {name: pool_blocks(lst) for name, lst in per_strat.items()}
    stats = summarize(pooled)
    print_report(stats, labels)
    return stats, labels


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--block-bars", type=int, default=BLOCK_BARS)
    args = ap.parse_args()
    print("Backtesting every live strategy over the full lookback and splitting "
          "results into ~6-month blocks (read-only)...")
    run(block_bars=args.block_bars)
