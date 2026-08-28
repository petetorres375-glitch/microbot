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


def test_finalize_uses_initial_risk_not_trailed_stop():
    """_finalize's R math must use the ORIGINAL risk (initial_risk), not the
    mutated s.stop_price — which _manage() ratchets up to breakeven on
    scale-out and further on every trail step. Using the live stop_price
    made entry - stop_price go negative for any winner whose trail moved
    past entry, silently logging r_multiple=0.0 despite a real profit
    (found 2026-08-28: 22 of 58 historical ORB trades affected, e.g. AEHR
    08-04 +$172.22 logged as 0.0R).
    """
    e = _engine()
    e._record_pnl = lambda pnl: None
    logged = {}
    e._log_closed = lambda sym, exit_price, reason, pnl, r, half_price=None: logged.update(
        {"pnl": pnl, "r": r}
    )

    s = ORBState(symbol="UMAC")
    s.qty_total = 49
    s.qty_remaining = 20
    s.entry_price = 21.22
    s.initial_risk = 21.22 - 20.21  # original stop, never re-derived from stop_price
    s.stop_price = 21.74            # trailed past entry — would flip the sign if used
    s.half_exited = True
    s.target_price = 23.24
    # scale-out already realized 29 shares' worth of gain; remainder stops at 21.74
    s.realized_pnl = 29 * (23.24 - 21.22) + 20 * (21.74 - 21.22)
    e.states["UMAC"] = s

    e._finalize("UMAC", 21.74, "stop")

    assert logged["pnl"] > 0
    assert logged["r"] > 0, "trailed winner must not be logged as 0.0R"
