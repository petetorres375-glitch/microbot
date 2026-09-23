import pytest

from microbot import journal, rebaseline, screener


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(journal.settings, "db_path", str(tmp_path / "t.db"))
    s = journal.settings
    monkeypatch.setattr(s, "universe", ["F"])
    monkeypatch.setattr(s, "include_dividend_stocks", False)
    monkeypatch.setattr(s, "include_split_stocks", False)
    monkeypatch.setattr(s, "include_ipo_stocks", False)
    monkeypatch.setattr(s, "include_discovered_stocks", True)
    monkeypatch.setattr(s, "universe_exclusions", ["AMD"])
    journal.init()
    for sym in ("XYZ", "AMD", "F"):
        i = journal.save_discovered_symbol({"symbol": sym, "sizing_ok": True})
        journal.approve_discovered_symbol(i)


def _screener_symbols(monkeypatch):
    scanned = []
    monkeypatch.setattr(screener, "MarketData", lambda: None)
    monkeypatch.setattr(screener, "_scan_symbols",
                        lambda md, syms, *a, **k: (scanned.extend(syms), ([], []))[1])
    screener.research()
    return scanned


def test_screener_includes_approved(db, monkeypatch):
    # XYZ added; AMD blocked by exclusions; F not duplicated
    assert _screener_symbols(monkeypatch) == ["F", "XYZ"]


def test_screener_flag_off(db, monkeypatch):
    monkeypatch.setattr(journal.settings, "include_discovered_stocks", False)
    assert _screener_symbols(monkeypatch) == ["F"]


def test_rebaseline_includes_approved(db):
    assert rebaseline._combined_universe()[0] == ["F", "XYZ"]


def test_rebaseline_flag_off(db, monkeypatch):
    monkeypatch.setattr(journal.settings, "include_discovered_stocks", False)
    assert rebaseline._combined_universe()[0] == ["F"]
