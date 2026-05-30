"""
risk.py
-------
Position sizing and risk gates. This is the most important file in the whole
project. A mediocre strategy with great risk control survives; a great strategy
with bad risk control eventually blows up.

Core rule: never risk more than `risk_per_trade_pct` of equity on one trade.
Risk per trade = (entry - stop) * shares. We solve for shares.

Reality check baked in:
  * Whole shares only (Alpaca bracket/OCO orders don't support fractional qty).
  * Skip a trade if even 1 share exceeds the risk budget or buying power.
  * Cap concurrent open risk across all positions (portfolio heat).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .strategies import Signal


@dataclass
class SizedTrade:
    signal: Signal
    qty: int
    dollar_risk: float
    notional: float

    def as_row(self):
        s = self.signal
        return {
            "symbol": s.symbol, "strategy": s.strategy, "side": s.side,
            "qty": self.qty, "entry": s.entry, "stop": s.stop,
            "target": s.target, "risk_per_share": s.risk_per_share,
            "dollar_risk": round(self.dollar_risk, 2),
            "notional": round(self.notional, 2), "rr": s.rr,
            "reason": s.reason,
        }


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 0.01    # risk 1% of equity per trade
    max_open_positions: int = 4         # don't over-diversify a tiny account
    max_portfolio_heat_pct: float = 0.04  # total open risk cap (4%)
    max_position_pct_of_equity: float = 0.34  # no single name > 34% of equity


def size_trade(signal: Signal, equity: float, buying_power: float,
               cfg: RiskConfig,
               open_risk: float = 0.0,
               open_positions: int = 0) -> Optional[SizedTrade]:
    """Return a SizedTrade, or None if the trade should be skipped."""
    if open_positions >= cfg.max_open_positions:
        return None
    if signal.risk_per_share <= 0:
        return None  # malformed stop

    risk_budget = equity * cfg.risk_per_trade_pct
    # Respect remaining portfolio heat.
    heat_remaining = equity * cfg.max_portfolio_heat_pct - open_risk
    risk_budget = min(risk_budget, heat_remaining)
    if risk_budget <= 0:
        return None

    qty = int(risk_budget // signal.risk_per_share)
    if qty < 1:
        return None  # one share already risks too much -> skip (honest)

    # Cap by buying power and by max position size.
    max_by_bp = int(buying_power // signal.entry)
    max_by_conc = int((equity * cfg.max_position_pct_of_equity) // signal.entry)
    qty = min(qty, max_by_bp, max_by_conc)
    if qty < 1:
        return None

    dollar_risk = qty * signal.risk_per_share
    notional = qty * signal.entry
    return SizedTrade(signal, qty, dollar_risk, notional)
