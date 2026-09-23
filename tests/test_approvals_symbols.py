import pytest

from microbot import approvals, journal


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(journal.settings, "db_path", str(tmp_path / "t.db"))
    journal.init()


def _save(symbol="XYZ", oos_trades=20):
    return journal.save_discovered_symbol({
        "symbol": symbol, "source": "trending", "strategy": "breakout",
        "is_expectancy_r": 0.3, "is_trades": 30, "oos_expectancy_r": 0.2,
        "oos_trades": oos_trades, "oos_win_rate": 0.55, "price": 25.0,
        "risk_per_share": 1.5, "sizing_ok": True})


def test_approve_and_reject(db):
    a, b = _save("AAA"), _save("BBB")
    assert approvals.approve_symbol(a)["ok"]
    assert approvals.reject_symbol(b)["ok"]
    assert journal.fetch_approved_universe() == ["AAA"]
    assert not approvals.approve_symbol(a)["ok"]      # already decided
    assert not approvals.reject_symbol(b)["ok"]


def test_low_sample_warning_only_below_threshold(db):
    thin = journal.get_discovered_symbol(_save("T", oos_trades=6))
    thick = journal.get_discovered_symbol(_save("K", oos_trades=40))
    assert any("low sample" in l for l in approvals._format_symbol(thin))
    assert not any("low sample" in l for l in approvals._format_symbol(thick))


def test_interactive_y_n_s(db, monkeypatch):
    for s in ("AAA", "BBB", "CCC"):
        _save(s)
    answers = iter(["y", "n", "s"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    approvals._interactive_symbols()
    assert journal.fetch_approved_universe() == ["AAA"]
    assert [r["symbol"] for r in journal.fetch_rejected_discovered()] == ["BBB"]
    assert [p["symbol"] for p in journal.fetch_pending_discovered()] == ["CCC"]
