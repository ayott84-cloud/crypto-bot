"""Module 2 Phase S0 — NYSE market calendar.

The crypto fleet has NO market-hours concept anywhere: every bot polls
24/7, every annualization divides by 365, and the risk sentinel's 30-min
staleness bar would fire hourly false alarms all night. A stocks module
cannot exist until "is the market open, and when does it close TODAY"
is answerable exactly.

Exactness matters beyond liveness: a 25-year backtest that misses a
holiday mis-dates every bar after it, and an EOD-flatten hardcoded to
15:55 fires THREE HOURS after a 13:00 half-day close.

Backend policy (anti-silent-degrade, the July lesson): prefer
pandas_market_calendars when installed; fall back to the built-in rule
engine otherwise; ALWAYS expose which backend is live via
backend_note() so a long-window run can print it. Never silently
diverge.

Run: python -m pytest tests/test_market_calendar.py -v
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

import market_calendar as mc

ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh=12, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


# ─── Backend transparency ──────────────────────────────────────────────────

def test_backend_note_is_explicit():
    note = mc.backend_note()
    assert isinstance(note, str) and note
    assert ("pandas_market_calendars" in note) or ("builtin" in note)


# ─── Weekends ──────────────────────────────────────────────────────────────

def test_weekends_closed():
    # 2026-08-01 is a Saturday, 08-02 a Sunday
    assert mc.is_trading_day(date(2026, 8, 1)) is False
    assert mc.is_trading_day(date(2026, 8, 2)) is False
    assert mc.is_market_open(_et(2026, 8, 1, 12)) is False


def test_ordinary_weekday_open_and_closed_hours():
    # 2026-08-03 is a Monday
    assert mc.is_trading_day(date(2026, 8, 3)) is True
    assert mc.is_market_open(_et(2026, 8, 3, 9, 29)) is False   # pre-open
    assert mc.is_market_open(_et(2026, 8, 3, 9, 30)) is True    # bell
    assert mc.is_market_open(_et(2026, 8, 3, 15, 59)) is True
    assert mc.is_market_open(_et(2026, 8, 3, 16, 0)) is False   # close is exclusive
    assert mc.is_market_open(_et(2026, 8, 3, 20, 0)) is False


# ─── 2026 holiday set (recurring rules + observed shifts) ─────────────────

@pytest.mark.parametrize("d,label", [
    (date(2026, 1, 1),  "New Year's Day (Thu)"),
    (date(2026, 1, 19), "MLK (3rd Mon Jan)"),
    (date(2026, 2, 16), "Washington's Birthday (3rd Mon Feb)"),
    (date(2026, 4, 3),  "Good Friday (Easter Apr 5)"),
    (date(2026, 5, 25), "Memorial Day (last Mon May)"),
    (date(2026, 6, 19), "Juneteenth (Fri)"),
    (date(2026, 7, 3),  "Independence Day OBSERVED (Jul 4 is a Sat)"),
    (date(2026, 9, 7),  "Labor Day (1st Mon Sep)"),
    (date(2026, 11, 26), "Thanksgiving (4th Thu Nov)"),
    (date(2026, 12, 25), "Christmas (Fri)"),
])
def test_2026_holidays_closed(d, label):
    assert mc.is_trading_day(d) is False, f"{d} should be closed: {label}"


def test_days_adjacent_to_holidays_are_open():
    assert mc.is_trading_day(date(2026, 1, 2)) is True     # day after New Year
    assert mc.is_trading_day(date(2026, 4, 2)) is True     # Maundy Thursday
    assert mc.is_trading_day(date(2026, 6, 18)) is True    # day before Juneteenth


# ─── Half days — the EOD-flatten killer ───────────────────────────────────

def test_half_days_2026_close_at_1300_et():
    """NYSE 2026 early closes: Nov 27 (day after Thanksgiving) and
    Dec 24 (Christmas Eve, since Dec 25 falls Friday)."""
    for d in (date(2026, 11, 27), date(2026, 12, 24)):
        assert mc.is_trading_day(d) is True, f"{d} is a trading day"
        assert mc.is_half_day(d) is True, f"{d} should be a half day"
        _, close_ts = mc.session_bounds(d)
        assert close_ts.astimezone(ET).hour == 13, f"{d} closes 13:00 ET"
        assert close_ts.astimezone(ET).minute == 0


def test_half_day_market_open_respects_early_close():
    assert mc.is_market_open(_et(2026, 11, 27, 12, 59)) is True
    assert mc.is_market_open(_et(2026, 11, 27, 13, 0)) is False
    assert mc.is_market_open(_et(2026, 11, 27, 15, 30)) is False   # would be open on a normal day


def test_ordinary_day_is_not_half_day():
    assert mc.is_half_day(date(2026, 8, 3)) is False
    _, close_ts = mc.session_bounds(date(2026, 8, 3))
    assert close_ts.astimezone(ET).hour == 16


def test_no_july_half_day_when_observed_holiday_takes_it():
    """2026: Jul 4 is Saturday -> Fri Jul 3 is the observed CLOSURE, so
    it must not also be reported as a half day."""
    assert mc.is_trading_day(date(2026, 7, 3)) is False
    assert mc.is_half_day(date(2026, 7, 3)) is False


# ─── Historical ad-hoc closures (25-year backtests hit these) ─────────────

@pytest.mark.parametrize("d,why", [
    (date(2001, 9, 11), "9/11 attacks"),
    (date(2001, 9, 12), "9/11 closure week"),
    (date(2012, 10, 30), "Hurricane Sandy"),
    (date(2018, 12, 5), "national day of mourning (G.H.W. Bush)"),
    (date(2025, 1, 9),  "national day of mourning (Carter)"),
])
def test_adhoc_closures_are_known(d, why):
    assert mc.is_trading_day(d) is False, f"{d} was closed: {why}"


# ─── Session navigation ───────────────────────────────────────────────────

def test_next_open_skips_weekend():
    # Friday 2026-07-31 after the close -> Monday 2026-08-03 09:30 ET
    nxt = mc.next_open(_et(2026, 7, 31, 17, 0))
    assert nxt.astimezone(ET).date() == date(2026, 8, 3)
    assert nxt.astimezone(ET).hour == 9 and nxt.astimezone(ET).minute == 30


def test_next_open_same_day_before_bell():
    nxt = mc.next_open(_et(2026, 8, 3, 6, 0))
    assert nxt.astimezone(ET).date() == date(2026, 8, 3)
    assert nxt.astimezone(ET).hour == 9


def test_next_open_skips_holiday():
    # Thanksgiving Thu 2026-11-26 -> next open is the Fri half day
    nxt = mc.next_open(_et(2026, 11, 26, 10, 0))
    assert nxt.astimezone(ET).date() == date(2026, 11, 27)


def test_session_bounds_are_timezone_aware_utc_convertible():
    open_ts, close_ts = mc.session_bounds(date(2026, 8, 3))
    assert open_ts.tzinfo is not None and close_ts.tzinfo is not None
    assert open_ts < close_ts
    assert open_ts.astimezone(timezone.utc) < close_ts.astimezone(timezone.utc)


# ─── Trading-day math (annualization inputs) ──────────────────────────────

def test_trading_days_between_excludes_weekends_and_holidays():
    # Mon 2026-11-23 .. Fri 2026-11-27 inclusive = 5 weekdays
    # minus Thanksgiving Thu 11-26 = 4 trading days
    assert mc.trading_days_between(date(2026, 11, 23), date(2026, 11, 27)) == 4


def test_trading_days_in_a_year_is_about_252():
    n = mc.trading_days_between(date(2026, 1, 1), date(2026, 12, 31))
    assert 248 <= n <= 254, f"got {n}, expected ~252"


def test_bars_to_years_daily():
    """252 daily bars ~= 1 year. The crypto path divides by 365 and would
    call this 0.69 years -- the exact error that mis-sized every stock
    annualization."""
    assert mc.bars_to_years(252, "1d") == pytest.approx(1.0, abs=0.02)
    assert mc.bars_to_years(2520, "1d") == pytest.approx(10.0, abs=0.2)


def test_bars_to_years_intraday_uses_session_length():
    """A 6.5h RTH session holds 78 5-minute bars, so one year ~= 19,656."""
    one_year_5m = mc.bars_to_years(78 * 252, "5m")
    assert one_year_5m == pytest.approx(1.0, abs=0.05)
    assert mc.bars_to_years(390 * 252, "1m") == pytest.approx(1.0, abs=0.05)


def test_bars_to_years_rejects_crypto_only_intervals():
    """4h/6h/8h/12h are meaningless inside a 6.5-hour session."""
    for iv in ("4h", "6h", "8h", "12h", "3d"):
        with pytest.raises(ValueError):
            mc.bars_to_years(100, iv)
