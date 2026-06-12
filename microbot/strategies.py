"""
strategies.py
-------------
Five systematic strategies, each targeting a different market regime edge:

  1. TrendMomentum  - rides established trends (EMA cross + ADX trend filter).
                      The backbone of CTA / managed-futures style trading.
  2. MeanReversion  - buys oversold pullbacks INSIDE an uptrend (RSI + Bollinger).
                      Classic swing-trading edge on liquid stocks.
  3. Breakout       - buys breakouts of an N-day high (Donchian / Turtle style),
                      with a volume confirmation.
  4. EMAullback    - triple-EMA alignment (21>50>150) + pullback to 21 EMA on
                      declining volume. Minervini / IBD Stage 2 style. More
                      selective than TrendMomentum — requires the full trend stack.
  5. Breakout52w    - 200-day high breakout with 1.5x volume. Turtle System 2 on
                      a longer scale. Catches institutional-grade breakouts that
                      the 20-day Donchian misses.

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


class EMAPullback(Strategy):
    """
    Triple-EMA alignment pullback (Minervini / IBD Stage 2 style).

    Requires EMA21 > EMA50 > EMA150 — the full uptrend stack — then enters when
    price pulls back to within one ATR of the 21 EMA on declining volume.
    RSI 38-62 confirms a healthy consolidation (not a breakdown, not a chase).

    Different from TrendMomentum: that strategy enters on any EMA20/50 cross or
    touch. This one demands all three EMAs aligned and uses volume + RSI to
    filter out false pullbacks.
    """
    name = "ema_pullback"

    def __init__(self, ema1=21, ema2=50, ema3=150, rsi_lo=38, rsi_hi=62, **kw):
        super().__init__(**kw)
        self.ema1, self.ema2, self.ema3 = ema1, ema2, ema3
        self.rsi_lo, self.rsi_hi = rsi_lo, rsi_hi

    def min_bars(self):
        return self.ema3 + self.atr_period + 5

    def evaluate(self, symbol, df):
        if len(df) < self.min_bars():
            return None
        close = df["close"]
        e1 = ind.ema(close, self.ema1)
        e2 = ind.ema(close, self.ema2)
        e3 = ind.ema(close, self.ema3)
        rsi_val = ind.rsi(close, 14)
        a = ind.atr(df, self.atr_period).iloc[-1]
        avg_vol = df["volume"].rolling(20).mean().iloc[-1]

        aligned = e1.iloc[-1] > e2.iloc[-1] > e3.iloc[-1]
        near_ema = abs(close.iloc[-1] - e1.iloc[-1]) <= a
        rsi_ok = self.rsi_lo <= rsi_val.iloc[-1] <= self.rsi_hi
        low_vol = df["volume"].iloc[-1] < avg_vol  # quiet pullback, not distribution

        if aligned and near_ema and rsi_ok and low_vol:
            why = (f"EMA{self.ema1}>{self.ema2}>{self.ema3}, "
                   f"pullback RSI={rsi_val.iloc[-1]:.0f}, low vol")
            return _bracket(symbol, self.name, close.iloc[-1], a,
                            self.stop_mult, self.rr, why)
        return None


class Breakout52w(Strategy):
    """
    200-day high breakout with volume confirmation (Turtle System 2 scale).

    Catches stocks making major new highs with institutional-level volume — a
    different quality of breakout than the 20-day Donchian in Breakout. A close
    above a 200-day high is a meaningful signal: the stock has cleared every
    seller from the past 10 months. Requires 1.5x volume for conviction.

    Will not fire on IPO stocks or recent listings (needs 200+ bars).
    """
    name = "breakout_52w"

    def __init__(self, lookback=200, vol_period=20, vol_mult=1.5, **kw):
        super().__init__(**kw)
        self.lookback = lookback
        self.vol_period, self.vol_mult = vol_period, vol_mult

    def min_bars(self):
        return self.lookback + self.atr_period + 5

    def evaluate(self, symbol, df):
        if len(df) < self.min_bars():
            return None
        close = df["close"]
        prior_high = df["high"].iloc[-(self.lookback + 1):-1].max()
        a = ind.atr(df, self.atr_period).iloc[-1]
        avg_vol = df["volume"].rolling(self.vol_period).mean().iloc[-1]
        vol_ok = df["volume"].iloc[-1] >= self.vol_mult * avg_vol

        if close.iloc[-1] > prior_high and vol_ok:
            why = (f"New {self.lookback}d-high on "
                   f"{df['volume'].iloc[-1] / avg_vol:.1f}x vol")
            return _bracket(symbol, self.name, close.iloc[-1], a,
                            self.stop_mult, self.rr, why)
        return None


class RSI2Reversion(Strategy):
    """
    Larry Connors' RSI(2) pullback — one of the most replicated documented
    edges in US equities (Connors & Alvarez, "Short Term Trading Strategies
    That Work").

    Buy an extreme short-term oversold reading (RSI(2) <= 10) only while the
    stock is above its 200-day SMA, and only when price is stretched below its
    5-day SMA (confirms the dip is acute, not a slow rollover). High win-rate,
    short-hold profile — complements MeanReversion, which uses RSI(14) + BB
    and fires far less often.

    Defaults are 1:1 reward:risk off a 3x ATR stop — validated 2026-06-12 over
    3.5y/49 symbols: 532 trades, 60% WR, +0.186R expectancy, PF 1.28. The
    standard 2:1/1.5x bracket tested at only +0.063R (mean reversion wants a
    wide stop and a near target, the opposite of a trend bracket).
    """
    name = "rsi2_reversion"

    def __init__(self, rsi_period=2, rsi_buy=10, trend_ma=200, stretch_ma=5, **kw):
        kw.setdefault("rr", 1.0)
        kw.setdefault("stop_mult", 3.0)
        super().__init__(**kw)
        self.rsi_period, self.rsi_buy = rsi_period, rsi_buy
        self.trend_ma, self.stretch_ma = trend_ma, stretch_ma

    def min_bars(self):
        return self.trend_ma + self.atr_period + 5

    def evaluate(self, symbol, df):
        if len(df) < self.min_bars():
            return None
        close = df["close"]
        rsi_fast = ind.rsi(close, self.rsi_period)
        long_trend = ind.sma(close, self.trend_ma)
        short_ma = ind.sma(close, self.stretch_ma)
        a = ind.atr(df, self.atr_period).iloc[-1]

        uptrend = close.iloc[-1] > long_trend.iloc[-1]
        oversold = rsi_fast.iloc[-1] <= self.rsi_buy
        stretched = close.iloc[-1] < short_ma.iloc[-1]
        if uptrend and oversold and stretched:
            why = (f"RSI({self.rsi_period})={rsi_fast.iloc[-1]:.0f}, "
                   f"below MA{self.stretch_ma}, > MA{self.trend_ma}")
            return _bracket(symbol, self.name, close.iloc[-1], a,
                            self.stop_mult, self.rr, why)
        return None


# Registry so config / dashboard can reference strategies by name.
ALL_STRATEGIES = {
    TrendMomentum.name: TrendMomentum,
    MeanReversion.name: MeanReversion,
    Breakout.name: Breakout,
    DividendMomentum.name: DividendMomentum,
    EMAPullback.name: EMAPullback,
    Breakout52w.name: Breakout52w,
    RSI2Reversion.name: RSI2Reversion,
}


def build_default_strategies(rr: float = 2.0):
    return [
        TrendMomentum(rr=rr),
        MeanReversion(rr=rr),
        Breakout(rr=rr),
        EMAPullback(rr=rr),
        Breakout52w(rr=rr),
        RSI2Reversion(),  # keeps its own validated 1:1 / 3x ATR bracket — do not pass rr
    ]


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
    return [
        _make(TrendMomentum),
        _make(MeanReversion),
        _make(Breakout),
        _make(EMAPullback),
        _make(Breakout52w),
        RSI2Reversion(**active.get(RSI2Reversion.name, {})),  # own rr — see class docstring
    ]
