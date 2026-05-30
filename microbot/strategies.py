"""
strategies.py
-------------
The three systematic strategies. Each is a different *market regime* edge, so
together they diversify:

  1. TrendMomentum  - rides established trends (EMA cross + ADX trend filter).
                      The backbone of CTA / managed-futures style trading.
  2. MeanReversion  - buys oversold pullbacks INSIDE an uptrend (RSI + Bollinger).
                      Classic swing-trading edge on liquid stocks.
  3. Breakout       - buys breakouts of an N-day high (Donchian / Turtle style),
                      with a volume confirmation.

Every strategy returns a Signal with an entry, a STOP, and a TARGET sized to a
2:1 reward:risk ratio off ATR. Direction is long-only here (short selling on a
$500 account is a different, riskier animal — left out of v1 on purpose).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from . import indicators as ind


@dataclass
class Signal:
    symbol: str
    strategy: str
    side: str          # "buy" (long-only in v1)
    entry: float       # reference price (last close)
    stop: float        # protective stop price
    target: float      # take-profit price (2:1 vs stop by construction)
    atr: float
    reason: str        # human-readable why
    rr: float = 2.0    # reward:risk

    @property
    def risk_per_share(self) -> float:
        return round(self.entry - self.stop, 4)

    @property
    def reward_per_share(self) -> float:
        return round(self.target - self.entry, 4)


def _bracket(symbol, strategy, entry, atr_val, stop_mult, rr, reason) -> Signal:
    """Build a long Signal with a 2:1 (rr) reward:risk off an ATR stop."""
    stop = entry - stop_mult * atr_val
    risk = entry - stop
    target = entry + rr * risk
    return Signal(symbol, strategy, "buy", round(entry, 2), round(stop, 2),
                  round(target, 2), round(atr_val, 4), reason, rr)


class Strategy:
    name = "base"

    def __init__(self, rr: float = 2.0, atr_period: int = 14, stop_mult: float = 1.5):
        self.rr = rr
        self.atr_period = atr_period
        self.stop_mult = stop_mult

    def evaluate(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        raise NotImplementedError

    def min_bars(self) -> int:
        return 60


class TrendMomentum(Strategy):
    name = "trend_momentum"

    def __init__(self, fast=20, slow=50, adx_min=20, **kw):
        super().__init__(**kw)
        self.fast, self.slow, self.adx_min = fast, slow, adx_min

    def min_bars(self):
        return self.slow + self.atr_period + 5

    def evaluate(self, symbol, df):
        if len(df) < self.min_bars():
            return None
        close = df["close"]
        fast = ind.ema(close, self.fast)
        slow = ind.ema(close, self.slow)
        adx = ind.adx(df, 14)
        a = ind.atr(df, self.atr_period).iloc[-1]

        crossed_up = fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1]
        trending = adx.iloc[-1] >= self.adx_min
        in_uptrend = fast.iloc[-1] > slow.iloc[-1] and close.iloc[-1] > slow.iloc[-1]

        # Enter on a fresh cross, OR while already trending up and pulling toward
        # the fast EMA (continuation entry).
        pullback = in_uptrend and close.iloc[-1] <= fast.iloc[-1] * 1.01
        if (crossed_up or pullback) and trending:
            why = f"EMA{self.fast}>{self.slow}, ADX={adx.iloc[-1]:.0f}"
            return _bracket(symbol, self.name, close.iloc[-1], a,
                            self.stop_mult, self.rr, why)
        return None


class MeanReversion(Strategy):
    name = "mean_reversion"

    def __init__(self, rsi_period=14, rsi_buy=32, bb_period=20, trend_ma=200, **kw):
        super().__init__(**kw)
        self.rsi_period, self.rsi_buy = rsi_period, rsi_buy
        self.bb_period, self.trend_ma = bb_period, trend_ma

    def min_bars(self):
        return self.trend_ma + self.atr_period + 5

    def evaluate(self, symbol, df):
        if len(df) < self.min_bars():
            return None
        close = df["close"]
        rsi = ind.rsi(close, self.rsi_period)
        _, _, lower = ind.bollinger(close, self.bb_period)
        long_trend = ind.sma(close, self.trend_ma)
        a = ind.atr(df, self.atr_period).iloc[-1]

        # Only buy dips that are still ABOVE the long-term trend (buy weakness in
        # an uptrend, never catch a falling knife in a downtrend).
        uptrend = close.iloc[-1] > long_trend.iloc[-1]
        oversold = rsi.iloc[-1] <= self.rsi_buy
        below_band = close.iloc[-1] <= lower.iloc[-1]
        if uptrend and oversold and below_band:
            why = f"RSI={rsi.iloc[-1]:.0f}, below lower BB, > MA{self.trend_ma}"
            return _bracket(symbol, self.name, close.iloc[-1], a,
                            self.stop_mult, self.rr, why)
        return None


class Breakout(Strategy):
    name = "breakout"

    def __init__(self, channel=20, vol_period=20, vol_mult=1.3, **kw):
        super().__init__(**kw)
        self.channel, self.vol_period, self.vol_mult = channel, vol_period, vol_mult

    def min_bars(self):
        return self.channel + self.atr_period + 5

    def evaluate(self, symbol, df):
        if len(df) < self.min_bars():
            return None
        close = df["close"]
        # Use the channel up to the PRIOR bar so today's bar can break it.
        upper, _ = ind.donchian(df.iloc[:-1], self.channel)
        a = ind.atr(df, self.atr_period).iloc[-1]
        avg_vol = df["volume"].rolling(self.vol_period).mean().iloc[-1]
        vol_ok = df["volume"].iloc[-1] >= self.vol_mult * avg_vol

        broke_out = close.iloc[-1] > upper.iloc[-1]
        if broke_out and vol_ok:
            why = f"Close>{self.channel}d-high on {df['volume'].iloc[-1]/avg_vol:.1f}x vol"
            return _bracket(symbol, self.name, close.iloc[-1], a,
                            self.stop_mult, self.rr, why)
        return None


class DividendMomentum(Strategy):
    """
    Designed for slow-trending, low-volatility dividend stocks.

    Uses longer EMAs (50/100) and a lower ADX threshold (15) because dividend
    names trend gradually. Stop is widened to 2x ATR to avoid getting shaken
    out by yield-driven day-to-day noise. Adds an RSI < 65 gate so we don't
    chase after a run-up that has already priced in the next dividend.
    """
    name = "dividend_momentum"

    def __init__(self, fast=50, slow=100, adx_min=15, rsi_max=65, **kw):
        kw.setdefault("stop_mult", 2.0)
        super().__init__(**kw)
        self.fast, self.slow = fast, slow
        self.adx_min, self.rsi_max = adx_min, rsi_max

    def min_bars(self):
        return self.slow + self.atr_period + 5

    def evaluate(self, symbol, df):
        if len(df) < self.min_bars():
            return None
        close = df["close"]
        fast = ind.ema(close, self.fast)
        slow = ind.ema(close, self.slow)
        adx_val = ind.adx(df, 14)
        rsi_val = ind.rsi(close, 14)
        a = ind.atr(df, self.atr_period).iloc[-1]

        in_uptrend = fast.iloc[-1] > slow.iloc[-1] and close.iloc[-1] > slow.iloc[-1]
        crossed_up = fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1]
        trending = adx_val.iloc[-1] >= self.adx_min
        not_overbought = rsi_val.iloc[-1] < self.rsi_max
        pullback = in_uptrend and close.iloc[-1] <= fast.iloc[-1] * 1.02

        if (crossed_up or pullback) and trending and not_overbought:
            why = (f"EMA{self.fast}>{self.slow}, ADX={adx_val.iloc[-1]:.0f}, "
                   f"RSI={rsi_val.iloc[-1]:.0f} (div play)")
            return _bracket(symbol, self.name, close.iloc[-1], a,
                            self.stop_mult, self.rr, why)
        return None


# Registry so config / dashboard can reference strategies by name.
ALL_STRATEGIES = {
    TrendMomentum.name: TrendMomentum,
    MeanReversion.name: MeanReversion,
    Breakout.name: Breakout,
    DividendMomentum.name: DividendMomentum,
}


def build_default_strategies(rr: float = 2.0):
    return [TrendMomentum(rr=rr), MeanReversion(rr=rr), Breakout(rr=rr)]


def build_dividend_strategies(rr: float = 2.0):
    """Strategies applied to the dividend universe (includes the standard set too)."""
    return [DividendMomentum(rr=rr), TrendMomentum(rr=rr), MeanReversion(rr=rr)]


def build_strategies_from_params(active: dict, rr: float = 2.0,
                                  dividend: bool = False) -> list:
    """
    Build strategy objects using params from the active_params DB table.
    Falls back to each strategy's hardcoded defaults for any key not stored.
    active: {strategy_name: {param: value, ...}} as returned by journal.fetch_active_params().
    """
    def _make(cls):
        return cls(rr=rr, **active.get(cls.name, {}))

    if dividend:
        return [_make(DividendMomentum), _make(TrendMomentum), _make(MeanReversion)]
    return [_make(TrendMomentum), _make(MeanReversion), _make(Breakout)]
