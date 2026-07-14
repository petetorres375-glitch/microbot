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

    def __init__(self, rr: float = 2.0, atr_period: int = 14, stop_mult: float = 1.5,
                 weekly_filter: bool = True):
        self.rr = rr
        self.atr_period = atr_period
        self.stop_mult = stop_mult
        self.weekly_filter = weekly_filter

    def _weekly_aligned(self, df: pd.DataFrame, cache: dict | None = None) -> bool:
        """True if the weekly trend is aligned (or weekly_filter is disabled)."""
        if not self.weekly_filter:
            return True
        if cache and "weekly_aligned" in cache:
            return bool(cache["weekly_aligned"].loc[df.index[-1]])
        return ind.weekly_ema_aligned(df)

    def evaluate(self, symbol: str, df: pd.DataFrame, cache: dict | None = None) -> Optional[Signal]:
        raise NotImplementedError

    def min_bars(self) -> int:
        return 60

    def precompute(self, df: pd.DataFrame) -> dict:
        """
        Compute every indicator series ONCE on the full history, so backtest's
        bar-by-bar walk-forward loop can look values up by date instead of
        recomputing them from scratch on a truncated window at every bar
        (previously O(n^2) — a single symbol/strategy pair over ~750 bars took
        6.7s; with a ~45-symbol universe x 7 strategies that's the entire
        10+ minute research delay). Every one of these is a rolling/EWM causal
        computation, so a value at date d computed on the full df is identical
        to one computed on any prefix window ending at or after d — no lookahead
        is introduced. The one exception (weekly_ema_aligned's resample-based
        current-week bucket) is deliberately NOT cached here and still
        recomputes per-bar in `_weekly_aligned`, since vectorizing it safely
        needs more care than a plain rolling/EWM series.
        Returns {} by default (falls back to the always-correct recompute path).
        """
        return {}


class TrendMomentum(Strategy):
    name = "trend_momentum"

    def __init__(self, fast=20, slow=50, adx_min=20, **kw):
        super().__init__(**kw)
        self.fast, self.slow, self.adx_min = fast, slow, adx_min

    def min_bars(self):
        return self.slow + self.atr_period + 5

    def precompute(self, df):
        close = df["close"]
        return {
            "fast": ind.ema(close, self.fast),
            "slow": ind.ema(close, self.slow),
            "adx": ind.adx(df, 14),
            "atr": ind.atr(df, self.atr_period),
            "weekly_aligned": ind.weekly_ema_aligned_series(df),
        }

    def evaluate(self, symbol, df, cache=None):
        if len(df) < self.min_bars():
            return None
        if not self._weekly_aligned(df, cache):
            return None
        close = df["close"]
        d, dp = df.index[-1], df.index[-2]
        if cache:
            fast_now, fast_prev = cache["fast"].loc[d], cache["fast"].loc[dp]
            slow_now, slow_prev = cache["slow"].loc[d], cache["slow"].loc[dp]
            adx_now = cache["adx"].loc[d]
            a = cache["atr"].loc[d]
        else:
            fast = ind.ema(close, self.fast)
            slow = ind.ema(close, self.slow)
            adx = ind.adx(df, 14)
            fast_now, fast_prev = fast.iloc[-1], fast.iloc[-2]
            slow_now, slow_prev = slow.iloc[-1], slow.iloc[-2]
            adx_now = adx.iloc[-1]
            a = ind.atr(df, self.atr_period).iloc[-1]

        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        trending = adx_now >= self.adx_min
        in_uptrend = fast_now > slow_now and close.iloc[-1] > slow_now

        # Enter on a fresh cross, OR while already trending up and pulling toward
        # the fast EMA (continuation entry).
        pullback = in_uptrend and close.iloc[-1] <= fast_now * 1.01
        if (crossed_up or pullback) and trending:
            why = f"EMA{self.fast}>{self.slow}, ADX={adx_now:.0f}"
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

    def precompute(self, df):
        close = df["close"]
        _, _, lower = ind.bollinger(close, self.bb_period)
        return {
            "rsi": ind.rsi(close, self.rsi_period),
            "lower_bb": lower,
            "trend_ma": ind.sma(close, self.trend_ma),
            "atr": ind.atr(df, self.atr_period),
            "weekly_aligned": ind.weekly_ema_aligned_series(df),
        }

    def evaluate(self, symbol, df, cache=None):
        if len(df) < self.min_bars():
            return None
        if not self._weekly_aligned(df, cache):
            return None
        close = df["close"]
        d = df.index[-1]
        if cache:
            rsi_now = cache["rsi"].loc[d]
            lower_now = cache["lower_bb"].loc[d]
            trend_now = cache["trend_ma"].loc[d]
            a = cache["atr"].loc[d]
        else:
            rsi_now = ind.rsi(close, self.rsi_period).iloc[-1]
            _, _, lower = ind.bollinger(close, self.bb_period)
            lower_now = lower.iloc[-1]
            trend_now = ind.sma(close, self.trend_ma).iloc[-1]
            a = ind.atr(df, self.atr_period).iloc[-1]

        # Only buy dips that are still ABOVE the long-term trend (buy weakness in
        # an uptrend, never catch a falling knife in a downtrend).
        uptrend = close.iloc[-1] > trend_now
        oversold = rsi_now <= self.rsi_buy
        below_band = close.iloc[-1] <= lower_now
        # Higher low confirms the dip is stabilising, not continuing lower.
        higher_low = df["low"].iloc[-1] > df["low"].iloc[-2]
        if uptrend and oversold and below_band and higher_low:
            why = f"RSI={rsi_now:.0f}, below lower BB, > MA{self.trend_ma}, higher low"
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

    def precompute(self, df):
        upper, _ = ind.donchian(df, self.channel)
        return {
            "donchian_upper": upper,  # look up at yesterday's date - see evaluate()
            "atr": ind.atr(df, self.atr_period),
            "vol_avg": df["volume"].rolling(self.vol_period).mean(),
            "weekly_aligned": ind.weekly_ema_aligned_series(df),
        }

    def evaluate(self, symbol, df, cache=None):
        if len(df) < self.min_bars():
            return None
        if not self._weekly_aligned(df, cache):
            return None
        close = df["close"]
        d, dp = df.index[-1], df.index[-2]
        if cache:
            # Full-df donchian at yesterday's date == donchian(df.iloc[:-1]) last
            # value in the windowed version (both are "channel-day high ending
            # yesterday", today deliberately excluded so today's bar can break it).
            upper_now = cache["donchian_upper"].loc[dp]
            a = cache["atr"].loc[d]
            avg_vol = cache["vol_avg"].loc[d]
        else:
            upper, _ = ind.donchian(df.iloc[:-1], self.channel)
            upper_now = upper.iloc[-1]
            a = ind.atr(df, self.atr_period).iloc[-1]
            avg_vol = df["volume"].rolling(self.vol_period).mean().iloc[-1]
        vol_ok = df["volume"].iloc[-1] >= self.vol_mult * avg_vol

        broke_out = close.iloc[-1] > upper_now
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

    def precompute(self, df):
        close = df["close"]
        return {
            "fast": ind.ema(close, self.fast),
            "slow": ind.ema(close, self.slow),
            "adx": ind.adx(df, 14),
            "rsi": ind.rsi(close, 14),
            "atr": ind.atr(df, self.atr_period),
            "weekly_aligned": ind.weekly_ema_aligned_series(df),
        }

    def evaluate(self, symbol, df, cache=None):
        if len(df) < self.min_bars():
            return None
        if not self._weekly_aligned(df, cache):
            return None
        close = df["close"]
        d, dp = df.index[-1], df.index[-2]
        if cache:
            fast_now, fast_prev = cache["fast"].loc[d], cache["fast"].loc[dp]
            slow_now, slow_prev = cache["slow"].loc[d], cache["slow"].loc[dp]
            adx_now = cache["adx"].loc[d]
            rsi_now = cache["rsi"].loc[d]
            a = cache["atr"].loc[d]
        else:
            fast = ind.ema(close, self.fast)
            slow = ind.ema(close, self.slow)
            adx_val = ind.adx(df, 14)
            rsi_val = ind.rsi(close, 14)
            fast_now, fast_prev = fast.iloc[-1], fast.iloc[-2]
            slow_now, slow_prev = slow.iloc[-1], slow.iloc[-2]
            adx_now = adx_val.iloc[-1]
            rsi_now = rsi_val.iloc[-1]
            a = ind.atr(df, self.atr_period).iloc[-1]

        in_uptrend = fast_now > slow_now and close.iloc[-1] > slow_now
        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        trending = adx_now >= self.adx_min
        not_overbought = rsi_now < self.rsi_max
        pullback = in_uptrend and close.iloc[-1] <= fast_now * 1.02

        if (crossed_up or pullback) and trending and not_overbought:
            why = (f"EMA{self.fast}>{self.slow}, ADX={adx_now:.0f}, "
                   f"RSI={rsi_now:.0f} (div play)")
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

    def precompute(self, df):
        close = df["close"]
        return {
            "e1": ind.ema(close, self.ema1),
            "e2": ind.ema(close, self.ema2),
            "e3": ind.ema(close, self.ema3),
            "rsi": ind.rsi(close, 14),
            "atr": ind.atr(df, self.atr_period),
            "vol_avg": df["volume"].rolling(20).mean(),
            "weekly_aligned": ind.weekly_ema_aligned_series(df),
        }

    def evaluate(self, symbol, df, cache=None):
        if len(df) < self.min_bars():
            return None
        if not self._weekly_aligned(df, cache):
            return None
        close = df["close"]
        d = df.index[-1]
        if cache:
            e1_now, e2_now, e3_now = cache["e1"].loc[d], cache["e2"].loc[d], cache["e3"].loc[d]
            rsi_now = cache["rsi"].loc[d]
            a = cache["atr"].loc[d]
            avg_vol = cache["vol_avg"].loc[d]
        else:
            e1_now = ind.ema(close, self.ema1).iloc[-1]
            e2_now = ind.ema(close, self.ema2).iloc[-1]
            e3_now = ind.ema(close, self.ema3).iloc[-1]
            rsi_now = ind.rsi(close, 14).iloc[-1]
            a = ind.atr(df, self.atr_period).iloc[-1]
            avg_vol = df["volume"].rolling(20).mean().iloc[-1]

        aligned = e1_now > e2_now > e3_now
        near_ema = abs(close.iloc[-1] - e1_now) <= a
        rsi_ok = self.rsi_lo <= rsi_now <= self.rsi_hi
        low_vol = df["volume"].iloc[-1] < avg_vol  # quiet pullback, not distribution

        if aligned and near_ema and rsi_ok and low_vol:
            why = (f"EMA{self.ema1}>{self.ema2}>{self.ema3}, "
                   f"pullback RSI={rsi_now:.0f}, low vol")
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

    def precompute(self, df):
        return {
            "prior_high_roll": df["high"].rolling(self.lookback).max(),
            "atr": ind.atr(df, self.atr_period),
            "vol_avg": df["volume"].rolling(self.vol_period).mean(),
            "weekly_aligned": ind.weekly_ema_aligned_series(df),
        }

    def evaluate(self, symbol, df, cache=None):
        if len(df) < self.min_bars():
            return None
        if not self._weekly_aligned(df, cache):
            return None
        close = df["close"]
        d, dp = df.index[-1], df.index[-2]
        if cache:
            # rolling(lookback).max() at yesterday's date == the same
            # lookback-day window ending yesterday (today excluded) that the
            # windowed slice computes.
            prior_high = cache["prior_high_roll"].loc[dp]
            a = cache["atr"].loc[d]
            avg_vol = cache["vol_avg"].loc[d]
        else:
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
        kw.setdefault("weekly_filter", False)  # validated without this filter; 200-day SMA handles it
        super().__init__(**kw)
        self.rsi_period, self.rsi_buy = rsi_period, rsi_buy
        self.trend_ma, self.stretch_ma = trend_ma, stretch_ma

    def min_bars(self):
        return self.trend_ma + self.atr_period + 5

    def precompute(self, df):
        close = df["close"]
        return {
            "rsi": ind.rsi(close, self.rsi_period),
            "long_trend": ind.sma(close, self.trend_ma),
            "short_ma": ind.sma(close, self.stretch_ma),
            "atr": ind.atr(df, self.atr_period),
        }

    def evaluate(self, symbol, df, cache=None):
        if len(df) < self.min_bars():
            return None
        close = df["close"]
        d = df.index[-1]
        if cache:
            rsi_now = cache["rsi"].loc[d]
            long_trend_now = cache["long_trend"].loc[d]
            short_ma_now = cache["short_ma"].loc[d]
            a = cache["atr"].loc[d]
        else:
            rsi_now = ind.rsi(close, self.rsi_period).iloc[-1]
            long_trend_now = ind.sma(close, self.trend_ma).iloc[-1]
            short_ma_now = ind.sma(close, self.stretch_ma).iloc[-1]
            a = ind.atr(df, self.atr_period).iloc[-1]

        uptrend = close.iloc[-1] > long_trend_now
        oversold = rsi_now <= self.rsi_buy
        stretched = close.iloc[-1] < short_ma_now
        if uptrend and oversold and stretched:
            why = (f"RSI({self.rsi_period})={rsi_now:.0f}, "
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
