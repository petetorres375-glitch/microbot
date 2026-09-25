"""Tests for feedback.compute_vetoes — the analyzer veto loop judges in R, not $."""
from microbot import feedback, journal


def _trades(rows):
    return [{"ts": f"2026-09-{i + 1:02d}T13:35:00+00:00", "symbol": sym,
             "strategy": strat, "qty": 1, "entry": 10.0, "exit": 10.0,
             "outcome": "stop" if pnl < 0 else "target", "pnl": pnl, "r_multiple": r}
            for i, (strat, sym, pnl, r) in enumerate(rows)]


def test_sizing_change_does_not_veto_positive_r_strategy(monkeypatch):
    # dividend_momentum's real shape: small winners at $5k sizing, then two
    # -1R losers at 10x sizing. Dollar average is negative, R average positive.
    rows = [("dividend_momentum", "ET", 20.0, 0.4)] * 6 + \
           [("dividend_momentum", "ET", -500.0, -1.0)] * 2
    monkeypatch.setattr(journal, "fetch_trades", lambda: _trades(rows))
    v = feedback.compute_vetoes()
    assert "dividend_momentum" not in v["setups"]
    assert ("dividend_momentum", "ET") not in v["combos"]


def test_negative_r_strategy_is_vetoed(monkeypatch):
    rows = [("breakout", "NOK", -50.0, -1.0)] * 5 + [("breakout", "NOK", 90.0, 1.8)]
    monkeypatch.setattr(journal, "fetch_trades", lambda: _trades(rows))
    v = feedback.compute_vetoes()
    assert "breakout" in v["setups"]
    assert ("breakout", "NOK") in v["combos"]


def test_below_min_trades_is_not_judged(monkeypatch):
    rows = [("rsi2_reversion", "F", -50.0, -1.0)] * 5
    monkeypatch.setattr(journal, "fetch_trades", lambda: _trades(rows))
    assert feedback.compute_vetoes()["setups"] == set()


def test_noisy_one_to_one_record_is_not_vetoed(monkeypatch):
    # rsi2_reversion's live record + 1W/1L: avg R is negative, but 2 wins in 6
    # at 1:1 is well within noise for a real ~55-60% strategy.
    rows = [("rsi2_reversion", "F", 50.0, 1.035), ("rsi2_reversion", "F", -45.0, -0.938),
            ("rsi2_reversion", "F", -45.0, -0.977), ("rsi2_reversion", "F", -45.0, -0.972),
            ("rsi2_reversion", "F", 50.0, 1.0), ("rsi2_reversion", "F", -45.0, -1.0)]
    monkeypatch.setattr(journal, "fetch_trades", lambda: _trades(rows))
    assert "rsi2_reversion" not in feedback.compute_vetoes()["setups"]


def test_clearly_bad_record_is_still_vetoed(monkeypatch):
    rows = [("rsi2_reversion", "F", 50.0, 1.035)] + [("rsi2_reversion", "F", -45.0, -0.97)] * 5
    monkeypatch.setattr(journal, "fetch_trades", lambda: _trades(rows))
    assert "rsi2_reversion" in feedback.compute_vetoes()["setups"]
