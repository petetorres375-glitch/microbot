import pytest

from microbot import journal


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(journal.settings, "db_path", str(tmp_path / "t.db"))
    journal.init()


def _cand(symbol="XYZ"):
    return {"symbol": symbol, "source": "trending", "strategy": "breakout",
            "is_expectancy_r": 0.3, "is_trades": 20,
            "oos_expectancy_r": 0.2, "oos_trades": 8, "oos_win_rate": 0.5,
            "price": 25.0, "risk_per_share": 1.5, "sizing_ok": True}


def test_save_then_pending(db):
    i = journal.save_discovered_symbol(_cand())
    pending = journal.fetch_pending_discovered()
    assert [p["id"] for p in pending] == [i]
    assert pending[0]["sizing_ok"] == 1


def test_approve_adds_to_universe(db):
    i = journal.save_discovered_symbol(_cand("abc"))
    assert journal.approve_discovered_symbol(i) is True
    assert journal.fetch_approved_universe() == ["ABC"]
    assert journal.fetch_pending_discovered() == []
    assert journal.get_discovered_symbol(i)["status"] == "approved"


def test_approve_twice_is_noop(db):
    i = journal.save_discovered_symbol(_cand())
    journal.approve_discovered_symbol(i)
    assert journal.approve_discovered_symbol(i) is False
    j = journal.save_discovered_symbol(_cand())
    assert journal.approve_discovered_symbol(j) is True
    assert journal.fetch_approved_universe() == ["XYZ"]


def test_reject(db):
    i = journal.save_discovered_symbol(_cand())
    journal.reject_discovered_symbol(i, "nah")
    assert journal.fetch_pending_discovered() == []
    assert journal.fetch_approved_universe() == []
    assert [r["symbol"] for r in journal.fetch_rejected_discovered()] == ["XYZ"]
    assert journal.get_discovered_symbol(i)["note"] == "nah"


def test_auto_rejected_save(db):
    i = journal.save_discovered_symbol(_cand(), status="rejected", note="sizing")
    row = journal.get_discovered_symbol(i)
    assert row["status"] == "rejected" and row["decided_ts"] and row["note"] == "sizing"
    assert journal.approve_discovered_symbol(i) is False
