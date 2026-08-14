"""Aug 14 2026 — the completeness check cried wolf on today's session.

The live daemon logged this on every poll:

  WARNING INCOMPLETE QQQ tiingo: 406 of 407 sessions
          — 1 interior gaps: 2026-08-14

2026-08-14 was the current session. An end-of-day vendor publishes a
day's bar after that day closes, so the newest session is legitimately
absent while the market is open and for some lag afterwards. Flagging it
is guaranteed to fire every cycle, and a warning that always fires is a
warning nobody reads — which is how a real gap would get missed.

completeness_report already encodes the symmetric case: absence BEFORE
the first bar is not a defect, because a symbol listed mid-window has no
earlier bars to give. Absence at the live edge is the same kind of
non-defect and needs the same treatment.

Only TODAY is excused. Excusing yesterday too would hide a genuine
one-day vendor outage, which is exactly the failure this check exists to
catch.

Run: python -m pytest tests/test_equity_completeness_edge.py -v
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pytest

pd = pytest.importorskip("pandas")

import market_calendar as mc
from tools._equity_bars import completeness_report


def _sessions_ending(last: date, n: int) -> list:
    """The n trading days up to and including `last`."""
    out, d = [], last
    while len(out) < n:
        if mc.is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def _frame(days: list):
    return pd.DataFrame({"close": [100.0] * len(days)},
                         index=pd.to_datetime(days))


def _recent_session(offset_days: int = 0) -> date:
    d = date.today() - timedelta(days=offset_days)
    while not mc.is_trading_day(d):
        d -= timedelta(days=1)
    return d


# ─── The live edge ───────────────────────────────────────────────────────

def test_todays_unpublished_session_is_not_a_gap():
    """The exact false alarm: 406 of 407, missing only today."""
    if not mc.is_trading_day(date.today()):
        pytest.skip("today is not a trading day")
    days = _sessions_ending(_recent_session(), 30)
    rep = completeness_report("QQQ", _frame(days[:-1]), days[0], days[-1])
    assert rep["complete"] is True
    assert date.today() not in rep["missing"]


def test_todays_session_is_reported_as_pending_not_missing():
    """Silencing it must not mean hiding it — the operator should still
    be able to see the series stops at yesterday."""
    if not mc.is_trading_day(date.today()):
        pytest.skip("today is not a trading day")
    days = _sessions_ending(_recent_session(), 30)
    rep = completeness_report("QQQ", _frame(days[:-1]), days[0], days[-1])
    assert rep.get("pending") == 1


# ─── What must still fire ────────────────────────────────────────────────

def test_a_genuine_interior_hole_still_fails():
    days = _sessions_ending(_recent_session(offset_days=10), 30)
    holed = days[:15] + days[16:]
    rep = completeness_report("QQQ", _frame(holed), days[0], days[-1])
    assert rep["complete"] is False
    assert days[15] in rep["missing"]


def test_a_missing_previous_session_still_fails():
    """Yesterday is NOT excused — a one-day vendor outage is precisely
    what this check is for."""
    if not mc.is_trading_day(date.today()):
        pytest.skip("today is not a trading day")
    days = _sessions_ending(_recent_session(), 30)
    rep = completeness_report("QQQ", _frame(days[:-2]), days[0], days[-1])
    assert rep["complete"] is False


def test_a_purely_historical_window_is_unaffected():
    """Nothing in a window that ended long ago is pending."""
    days = _sessions_ending(date(2024, 6, 28), 30)
    rep = completeness_report("QQQ", _frame(days), days[0], days[-1])
    assert rep["complete"] is True
    assert rep.get("pending", 0) == 0


def test_leading_absence_is_still_excused():
    """The pre-existing rule must survive the change."""
    days = _sessions_ending(date(2024, 6, 28), 30)
    rep = completeness_report("NEWLISTING", _frame(days[10:]),
                                days[0], days[-1])
    assert rep["complete"] is True
    assert rep["leading_absent"] == 10
