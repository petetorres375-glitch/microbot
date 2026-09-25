"""Tests for feedback.compute_vetoes — the analyzer veto loop judges in R, not $."""
import pytest

from microbot import feedback, journal


@pytest.fixture(autouse=True)
def _no_real_journal(monkeypatch):
    # Default: no promoted params, no orders — tests opt in where needed.
    monkeypatch.setattr(journal, "fetch_promoted_dates", lambda: {})
    monkeypatch.setattr(journal, "fetch_orders", lambda limit=200: [])


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


def test_trades_entered_before_retune_are_ignored(monkeypatch):
    # 6 losers under old params, then a retune: the new version starts clean.
    rows = [("trend_momentum", "GOOG", -50.0, -1.0)] * 6
    monkeypatch.setattr(journal, "fetch_trades", lambda: _trades(rows))
    monkeypatch.setattr(journal, "fetch_promoted_dates",
                        lambda: {"trend_momentum": "2026-09-20T00:00:00+00:00"})
    assert feedback.compute_vetoes()["setups"] == set()


def test_window_uses_entry_time_not_close_time(monkeypatch):
    # Closed after the retune but ENTERED before it -> old params, excluded.
    rows = [("breakout", "NOK", -50.0, -1.0)] * 6   # closes 2026-09-01..06
    monkeypatch.setattr(journal, "fetch_trades", lambda: _trades(rows))
    monkeypatch.setattr(journal, "fetch_promoted_dates",
                        lambda: {"breakout": "2026-08-15T00:00:00+00:00"})
    monkeypatch.setattr(journal, "fetch_orders", lambda limit=200: [
        {"ts": "2026-08-10T13:35:00+00:00", "symbol": "NOK", "strategy": "breakout"}])
    assert feedback.compute_vetoes()["setups"] == set()

    # Same trades entered after the retune -> they count, and they're clearly losing.
    monkeypatch.setattr(journal, "fetch_orders", lambda limit=200: [
        {"ts": "2026-08-20T13:35:00+00:00", "symbol": "NOK", "strategy": "breakout"}])
    assert "breakout" in feedback.compute_vetoes()["setups"]


def test_never_promoted_strategy_keeps_full_history(monkeypatch):
    rows = [("manual", "F", -50.0, -1.0)] * 6
    monkeypatch.setattr(journal, "fetch_trades", lambda: _trades(rows))
    monkeypatch.setattr(journal, "fetch_promoted_dates",
                        lambda: {"breakout": "2026-09-20T00:00:00+00:00"})
    assert "manual" in feedback.compute_vetoes()["setups"]
