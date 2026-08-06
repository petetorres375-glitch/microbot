"""ORB range-validity tests — see PRCT 2026-08-06 (commit pending): a zero-width
9:30 opening bar (high == low, a single print) was used as a real stop level and
got tagged almost immediately for -1.1R. _set_orb should reject that range
instead of treating it as tradeable support/resistance.
"""
from microbot.intraday_engine import IntradayEngine, ORBState


def _engine():
    e = IntradayEngine.__new__(IntradayEngine)
    e.states = {}
    return e


def test_set_orb_rejects_zero_width_range():
    e = _engine()
    e.states["PRCT"] = ORBState(symbol="PRCT")
    e._set_orb("PRCT", high=18.20, low=18.20)

    s = e.states["PRCT"]
    assert s.orb_invalid is True
    assert s.orb_established is False


def test_set_orb_accepts_normal_range():
    e = _engine()
    e.states["PAVS"] = ORBState(symbol="PAVS")
    e._set_orb("PAVS", high=9.20, low=8.23)

    s = e.states["PAVS"]
    assert s.orb_invalid is False
    assert s.orb_established is True
    assert s.orb_high == 9.20
    assert s.orb_low == 8.23


def test_invalid_orb_excluded_from_pending():
    e = _engine()
    e.states["PRCT"] = ORBState(symbol="PRCT", orb_invalid=True)
    e.states["PAVS"] = ORBState(symbol="PAVS")

    pending = [sym for sym, s in e.states.items()
               if not s.orb_established and not s.orb_invalid and not s.closed]

    assert pending == ["PAVS"]
