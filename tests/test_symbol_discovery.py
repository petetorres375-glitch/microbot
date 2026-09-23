import numpy as np
import pandas as pd
import pytest

from microbot import journal, symbol_discovery as sd

N_BARS = 400  # IS/OOS cutoff at bar 300


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(journal.settings, "db_path", str(tmp_path / "t.db"))
    monkeypatch.setattr(sd.settings, "discovery_sizing_equity", 5000.0)  # $50 risk budget
    monkeypatch.setattr(sd.settings, "starting_equity", 50000.0)         # inflated paper equity
    monkeypatch.setattr(sd.settings, "universe_exclusions", ["AMD", "ALAB", "GOOG"])
    journal.init()


def _df(price: float, daily_range: float) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=N_BARS)
    close = np.full(N_BARS, price)
    return pd.DataFrame({"open": close, "high": close + daily_range / 2,
                         "low": close - daily_range / 2, "close": close,
                         "volume": np.full(N_BARS, 1e6)}, index=idx)


class FakeStrat:
    def __init__(self, name, trades):
        self.name, self._trades = name, trades
        self.atr_period, self.stop_mult, self.rr = 14, 2.0, 2.0

    def min_bars(self):
        return 60


def _trades(is_rs, oos_rs):
    """IS trades enter before bar 300, OOS trades at/after it."""
    return ([{"entry_idx": 10 + i, "r_multiple": r} for i, r in enumerate(is_rs)]
            + [{"entry_idx": 300 + i, "r_multiple": r} for i, r in enumerate(oos_rs)])


@pytest.fixture
def fake_backtest(monkeypatch):
    monkeypatch.setattr(sd, "backtest_symbol", lambda strat, sym, df: strat._trades)


GOOD = _trades([1, 1, -1, 1, 1, -1], [1, 1, -1, 1, 1])   # IS +0.33R, OOS +0.6R


def test_good_candidate_is_pending(db, fake_backtest):
    row, status, note = sd._evaluate_candidate(
        {"symbol": "XYZ", "source": "trending"}, _df(20, 1.0), [FakeStrat("s", GOOD)])
    assert status == "pending", note
    assert row["sizing_ok"] and row["oos_trades"] == 5 and row["oos_expectancy_r"] == 0.6


def test_amd_shaped_candidate_fails_sizing(db, fake_backtest):
    # $500 stock, ~$40 ATR, 2x ATR stop = $80/share risk > $50 budget
    row, status, note = sd._evaluate_candidate(
        {"symbol": "BIG"}, _df(500, 40.0), [FakeStrat("s", GOOD)])
    assert status == "rejected" and "sizing" in note
    assert row["sizing_ok"] is False


def test_sizing_uses_live_equity_not_paper_equity(db, fake_backtest, monkeypatch):
    # AMD's real 2026-09-22 shape: $624, ATR ~$26.5 -> 2x ATR stop = $53/share.
    # Fits the $500 budget of $50k paper equity, but not the $50 budget at $5k live.
    amd_like = _df(624, 26.5)
    _, status, note = sd._evaluate_candidate({"symbol": "Q"}, amd_like, [FakeStrat("s", GOOD)])
    assert status == "rejected" and "sizing" in note
    monkeypatch.setattr(sd.settings, "discovery_sizing_equity", 50000.0)
    _, status, _ = sd._evaluate_candidate({"symbol": "Q"}, amd_like, [FakeStrat("s", GOOD)])
    assert status == "pending"


def test_thin_oos_sample_rejected(db, fake_backtest):
    thin = _trades([1, 1, -1, 1, 1], [1, 1])
    _, status, note = sd._evaluate_candidate({"symbol": "X"}, _df(20, 1.0), [FakeStrat("s", thin)])
    assert status == "rejected" and "oos sample" in note


def test_negative_oos_rejected(db, fake_backtest):
    decays = _trades([1, 1, 1, 1, 1], [-1, -1, -1, 1, -1])
    _, status, note = sd._evaluate_candidate({"symbol": "X"}, _df(20, 1.0), [FakeStrat("s", decays)])
    assert status == "rejected" and "oos expectancy" in note


def test_strategy_picked_by_in_sample_not_oos(db, fake_backtest):
    # 'a' has better IS, worse OOS; 'b' is the reverse. The pick must be 'a'.
    a = FakeStrat("a", _trades([1] * 6, [-1] * 5))
    b = FakeStrat("b", _trades([1, -1] * 3, [1] * 5))
    row, status, _ = sd._evaluate_candidate({"symbol": "X"}, _df(20, 1.0), [a, b])
    assert row["strategy"] == "a" and status == "rejected"


class FakeMD:
    def bars(self, symbol, **kw):
        return _df(20, 1.0)


def _run(monkeypatch, symbols, **kw):
    monkeypatch.setattr(sd.yahoo_scanner, "fetch_candidates",
                        lambda top: [{"symbol": s, "source": "trending"} for s in symbols])
    monkeypatch.setattr(sd.yahoo_scanner, "_current_universe", lambda: {"F"})
    monkeypatch.setattr(sd, "_live_strategies", lambda: [FakeStrat("s", GOOD)])
    return sd.run_discovery(md=FakeMD(), **kw)


def test_run_skips_exclusions_and_saves(db, fake_backtest, monkeypatch):
    rows = _run(monkeypatch, ["AMD", "F", "XYZ"])
    assert [r["symbol"] for r in rows] == ["XYZ"]
    assert [p["symbol"] for p in journal.fetch_pending_discovered()] == ["XYZ"]


def test_run_does_not_requeue_pending_or_rejected(db, fake_backtest, monkeypatch):
    _run(monkeypatch, ["XYZ"])
    pid = journal.fetch_pending_discovered()[0]["id"]
    journal.reject_discovered_symbol(pid, "no")
    assert _run(monkeypatch, ["XYZ"], force=True) == []


def test_weekly_throttle(db, fake_backtest, monkeypatch):
    assert len(_run(monkeypatch, ["XYZ"])) == 1
    assert _run(monkeypatch, ["ABC"]) == []                 # throttled
    assert len(_run(monkeypatch, ["ABC"], force=True)) == 1  # force overrides


def _reject(symbol, note, days_ago):
    import sqlite3
    from datetime import datetime, timedelta, timezone
    i = journal.save_discovered_symbol({"symbol": symbol}, status="rejected", note=note)
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    with sqlite3.connect(journal.settings.db_path) as con:
        con.execute("UPDATE discovered_symbols SET decided_ts=? WHERE id=?", (ts, i))


@pytest.mark.parametrize("note,days_ago,blocked", [
    ("oos sample < 5 trades", 10, True),                  # thin sample, too recent
    ("oos sample < 5 trades", 91, False),                 # thin sample, retry now
    ("no strategy with 5+ in-sample trades", 91, False),
    ("sizing: 1 share exceeds risk budget", 10, True),
    ("sizing: 1 share exceeds risk budget", 91, False),   # account may have grown
    ("oos expectancy <= 0", 400, True),                   # losing backtest: permanent
    ("in-sample expectancy <= 0", 400, True),
    ("user rejected", 400, True),                         # human no: permanent
])
def test_rejection_retry_rules(db, note, days_ago, blocked):
    _reject("XYZ", note, days_ago)
    assert ("XYZ" in sd._blocked_rejections()) is blocked


def test_any_permanent_rejection_wins(db):
    _reject("XYZ", "oos sample < 5 trades", 200)
    _reject("XYZ", "oos expectancy <= 0", 200)
    assert "XYZ" in sd._blocked_rejections()


def test_expired_thin_sample_is_reevaluated(db, fake_backtest, monkeypatch):
    _reject("XYZ", "oos sample < 5 trades", 91)
    rows = _run(monkeypatch, ["XYZ"])
    assert [r["symbol"] for r in rows] == ["XYZ"] and rows[0]["status"] == "pending"
