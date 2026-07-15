"""
optimizer.py
------------
Walk-forward parameter optimizer.  Safe by design:

  1. Downloads fresh bars once per run.
  2. Splits history: first IS_SPLIT (75%) is in-sample, the rest is OOS.
  3. Grid-searches each strategy's parameter space on the IS window.
  4. Validates the top-N IS candidates on the OOS window to catch overfit params.
  5. If the best OOS candidate beats current active params by >= MIN_IMPROVEMENT_PCT,
     a proposal is saved to the database for human review.

Nothing is promoted automatically.  Proposals sit in param_proposals until you
approve them via `python -m microbot.approvals --params` or the dashboard.
"""
from __future__ import annotations

import inspect
import itertools
import json
import math
from typing import Dict, List

from .backtest import backtest_symbol
from .data import MarketData
from . import journal
from . import metrics
from . import notify
from .config import settings
from .strategies import (
    TrendMomentum, MeanReversion, Breakout, DividendMomentum, Strategy,
)

# Minimum OOS improvement over current params to bother creating a proposal.
MIN_IMPROVEMENT_PCT = 15.0

# Fraction of each symbol's history that goes into the in-sample window.
IS_SPLIT = 0.75

# How many top IS combos to cross-validate on OOS (avoids running the full grid OOS).
TOP_N = 5

PARAM_GRIDS: Dict[str, Dict] = {
    TrendMomentum.name: {
        "fast":      [10, 20, 30],
        "slow":      [40, 50, 60],
        "adx_min":   [15, 20, 25],
        "stop_mult": [1.0, 1.5, 2.0],
    },
    MeanReversion.name: {
        "rsi_buy":   [28, 32, 36],
        "bb_period": [15, 20, 25],
        "stop_mult": [1.0, 1.5, 2.0],
    },
    Breakout.name: {
        "channel":   [15, 20, 25],
        "vol_mult":  [1.2, 1.3, 1.5],
        "stop_mult": [1.0, 1.5, 2.0],
    },
    DividendMomentum.name: {
        "fast":      [40, 50, 60],
        "slow":      [80, 100, 120],
        "adx_min":   [10, 15, 20],
        "stop_mult": [1.5, 2.0, 2.5],
    },
}

STRATEGY_CLASSES = {
    TrendMomentum.name:    TrendMomentum,
    MeanReversion.name:    MeanReversion,
    Breakout.name:         Breakout,
    DividendMomentum.name: DividendMomentum,
}


def _grid(param_dict: Dict) -> List[Dict]:
    keys = list(param_dict.keys())
    return [dict(zip(keys, vals))
            for vals in itertools.product(*param_dict.values())]


def _universe_score(strat: Strategy, df_dict: Dict) -> float:
    """Aggregate expectancy_r * sqrt(trades) across all symbols."""
    total = 0.0
    for sym, df in df_dict.items():
        if len(df) < strat.min_bars() + 10:
            continue
        trades = backtest_symbol(strat, sym, df)
        m = metrics.compute(trades)
        if m["trades"] >= 3 and (m["profit_factor"] or 0) >= 1.0:
            total += m["expectancy_r"] * math.sqrt(m["trades"])
    return round(total, 4)


def _default_params(strat_name: str) -> Dict:
    cls = STRATEGY_CLASSES[strat_name]
    sig = inspect.signature(cls.__init__)
    return {
        name: param.default
        for name, param in sig.parameters.items()
        if name not in ("self", "rr", "atr_period")
        and not name.startswith("**")
        and param.default is not inspect.Parameter.empty
    }


def _optimize_one(strat_name: str, df_is: Dict, df_oos: Dict,
                  rr: float, current_params: Dict) -> Dict | None:
    cls = STRATEGY_CLASSES[strat_name]
    grid = _grid(PARAM_GRIDS[strat_name])
    total_combos = len(grid)
    print(f"  [{strat_name}] {total_combos} combos × {len(df_is)} symbols...", flush=True)

    # IS grid search.
    is_results = []
    for params in grid:
        strat = cls(rr=rr, **params)
        score = _universe_score(strat, df_is)
        is_results.append((score, params))
    is_results.sort(key=lambda x: x[0], reverse=True)

    # OOS validation of top N IS candidates.
    oos_results = []
    for is_score, params in is_results[:TOP_N]:
        strat = cls(rr=rr, **params)
        oos_score = _universe_score(strat, df_oos)
        oos_results.append((oos_score, is_score, params))
    oos_results.sort(key=lambda x: x[0], reverse=True)
    best_oos, best_is, best_params = oos_results[0]

    # Baseline: current active params on the same OOS window.
    baseline = cls(rr=rr, **current_params)
    current_oos = _universe_score(baseline, df_oos)

    improvement = ((best_oos - current_oos) / max(abs(current_oos), 1e-6)) * 100
    print(f"    proposed OOS={best_oos:.3f}  current OOS={current_oos:.3f}"
          f"  Δ={improvement:+.1f}%")

    if improvement < MIN_IMPROVEMENT_PCT:
        print(f"    no proposal — improvement < {MIN_IMPROVEMENT_PCT}%")
        return None

    return {
        "strategy":         strat_name,
        "proposed_params":  best_params,
        "current_params":   current_params,
        "is_score":         best_is,
        "oos_score":        best_oos,
        "current_oos_score": current_oos,
        "improvement_pct":  round(improvement, 2),
    }


def _full_universe() -> List[str]:
    seen: set = set()
    out: List[str] = []
    for sym in (list(settings.universe)
                + (list(settings.dividend_universe) if settings.include_dividend_stocks else [])
                + (list(settings.split_universe) if settings.include_split_stocks else [])):
        sym = sym.strip().upper()
        if sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


def run_optimization(universe: List[str] | None = None,
                     rr: float | None = None,
                     strategies: List[str] | None = None) -> List[Dict]:
    """
    Download bars, run walk-forward grid search for all strategies (or just
    `strategies` if given), save proposals to the DB. Returns the list of
    proposals created this run.
    """
    journal.init()
    universe = universe or _full_universe()
    rr = rr or settings.reward_risk_ratio
    grid_names = [s for s in PARAM_GRIDS if strategies is None or s in strategies]

    print(f"\n=== microbot optimizer | {len(universe)} symbols ===")
    md = MarketData()
    df_full: Dict = {}
    for sym in universe:
        try:
            df = md.bars(sym)
            if df is not None and len(df) >= 120:
                df_full[sym] = df
        except Exception as e:
            print(f"  ! {sym}: {e}")
    print(f"  {len(df_full)} symbols loaded. Splitting {IS_SPLIT:.0%}/{1 - IS_SPLIT:.0%}...")

    df_is: Dict = {}
    df_oos: Dict = {}
    for sym, df in df_full.items():
        cut = int(len(df) * IS_SPLIT)
        if cut < 60 or (len(df) - cut) < 30:
            continue
        df_is[sym] = df.iloc[:cut].copy()
        df_oos[sym] = df.iloc[cut:].copy()

    active = journal.fetch_active_params()
    proposals_created: List[Dict] = []

    for strat_name in grid_names:
        current_params = active.get(strat_name, _default_params(strat_name))
        result = _optimize_one(strat_name, df_is, df_oos, rr, current_params)
        if result is None:
            continue
        pid = journal.save_param_proposal(result)
        result["proposal_id"] = pid
        proposals_created.append(result)
        print(f"  -> proposal #{pid} queued for {strat_name} "
              f"(+{result['improvement_pct']:.1f}% OOS)\n"
              f"     params: {json.dumps(result['proposed_params'])}")

    print(f"\n{len(proposals_created)} proposal(s) saved. "
          f"Review: python -m microbot.approvals --params")

    if proposals_created:
        names = ", ".join(p["strategy"] for p in proposals_created)
        notify.notify_proposals(proposals_created,
                                msg=f"Optimizer found improvements for: {names}")

    return proposals_created
