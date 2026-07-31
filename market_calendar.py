"""Module 2 Phase S0 — NYSE trading calendar.

The crypto fleet has no market-hours concept: every bot polls 24/7,
every annualization divides by 365, and the risk sentinel's staleness
bar assumes a heartbeat every few minutes forever. None of that
survives contact with a market that is shut 17 hours a day, all
weekend, ~10 holidays a year, and closes at 13:00 on a handful of days
nobody remembers until an EOD-flatten fires three hours late.

BACKEND POLICY (the July anti-silent-degrade lesson).
`pandas_market_calendars` is preferred when installed — it carries the
authoritative schedule including ad-hoc closures. Without it we fall
back to a built-in rule engine: exact for every recurring holiday
(computed, not tabulated) plus an explicit table of historical ad-hoc
closures. Either way `backend_note()` names the live backend so a
long-window run can print it. The one thing we never do is quietly
disagree with reality.

Public surface:
    backend_note()                     -> str
    is_trading_day(d)                  -> bool
    is_half_day(d)                     -> bool
    is_market_open(ts)                 -> bool
    session_bounds(d)                  -> (open_dt, close_dt)  tz-aware
    next_open(ts)                      -> tz-aware datetime
    trading_days_between(a, b)         -> int   (inclusive)
    bars_to_years(bars, interval)      -> float
"""

from __future__ import annotations

import datetime as _dt
from functools import lru_cache
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

REGULAR_OPEN = _dt.time(9, 30)
REGULAR_CLOSE = _dt.time(16, 0)
HALF_DAY_CLOSE = _dt.time(13, 0)

# Trading days per year (NYSE long-run average) — the equity counterpart
# to the crypto path's 365.
TRADING_DAYS_PER_YEAR = 252.0
REGULAR_SESSION_MINUTES = 390          # 09:30 -> 16:00

# Equity-valid bar intervals. 4h/6h/8h/12h/3d exist in the crypto
# vocabulary and are meaningless inside a 6.5-hour session — asking for
# them is a bug, not a preference, so we raise.
_INTERVAL_MINUTES = {
    "1m": 1, "2m": 2, "5m": 5, "10m": 10, "15m": 15, "30m": 30,
    "1h": 60, "1d": REGULAR_SESSION_MINUTES, "1w": REGULAR_SESSION_MINUTES * 5,
}

# ─── Ad-hoc closures the recurring rules cannot know ──────────────────────
# Unscheduled full-day closures. A 25-year backtest walks straight
# through these; without them every bar after 2001-09-10 is mis-dated.
_ADHOC_CLOSURES = frozenset({
    _dt.date(2001, 9, 11), _dt.date(2001, 9, 12),
    _dt.date(2001, 9, 13), _dt.date(2001, 9, 14),   # 9/11 closure week
    _dt.date(2004, 6, 11),                           # Reagan funeral
    _dt.date(2007, 1, 2),                            # Ford funeral
    _dt.date(2012, 10, 29), _dt.date(2012, 10, 30),  # Hurricane Sandy
    _dt.date(2018, 12, 5),                           # G.H.W. Bush funeral
    _dt.date(2025, 1, 9),                            # Carter funeral
})

# Unscheduled/irregular EARLY closes (1pm). Recurring half-days are
# computed below; these are the one-offs that no rule can derive.
#
# 2003-12-26 must be TABULATED, not ruled: the NYSE did NOT early-close
# on Dec 26 in 2008 or 2014, both also Fridays after Christmas.
_ADHOC_HALF_DAYS = frozenset({
    _dt.date(2003, 12, 26),     # Friday after Christmas Day (13:00)
})

# Sessions that opened LATE. Adversarial verification (Jul 31 2026)
# caught 2001-09-17 encoded above as an early close: the 9/11 reopen was
# a full session to 16:00 that merely started at 09:33 — calling it a
# 13:00 close silently deleted the last three hours of a record-volume
# day. 2002-09-11 is the materially large one: a noon open makes a
# 4-hour session, shorter than any half day this module models.
_ADHOC_LATE_OPENS = {
    _dt.date(2001, 9, 17):  _dt.time(9, 33),   # 9/11 reopen
    _dt.date(2001, 10, 8):  _dt.time(9, 31),
    _dt.date(2002, 9, 11):  _dt.time(12, 0),   # 1yr memorial
    _dt.date(2003, 3, 20):  _dt.time(9, 32),
    _dt.date(2004, 6, 7):   _dt.time(9, 32),
    _dt.date(2006, 12, 27): _dt.time(9, 32),
}

# Hard floor for the builtin engine. Below this the recurring rules are
# not merely incomplete but WRONG: the Monday-ized Washington's Birthday
# and Memorial Day date from the 1971 Uniform Monday Holiday Act,
# Election Day closures ran through 1980, and Saturday sessions ran to
# June 1952. Returning a confident wrong answer is precisely the failure
# mode this project keeps killing, so we raise instead. (The binding
# constraint is the MLK >= 1998 gate.)
EARLIEST_SUPPORTED = _dt.date(1998, 1, 1)

# Year floors for the recurring half-day series, verified against the
# NYSE's own closings document. Without these the engine projects modern
# conventions arbitrarily far back and invents half days.
_DAY_AFTER_THANKSGIVING_FROM = 1993
_CHRISTMAS_EVE_HALF_FROM = 1999
_JULY3_FROM = 1995          # Mon/Tue/Thu placements
_JULY3_WEDNESDAY_FROM = 2013   # the Wednesday convention is modern
_JULY5_FRIDAY_RULE = (1996, 2012)   # pre-2013: Thu Jul 4 shifted to Fri


def _easter(year: int) -> _dt.date:
    """Anonymous Gregorian computus. Verified: 2026 -> Apr 5."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return _dt.date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> _dt.date:
    """n-th `weekday` (Mon=0) of a month; n=-1 means the last one."""
    if n > 0:
        first = _dt.date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + _dt.timedelta(days=offset + 7 * (n - 1))
    last_day = (_dt.date(year + (month == 12), (month % 12) + 1, 1)
                 - _dt.timedelta(days=1))
    offset = (last_day.weekday() - weekday) % 7
    return last_day - _dt.timedelta(days=offset)


def _observed(d: _dt.date) -> _dt.date:
    """NYSE weekend-observance: Saturday -> preceding Friday,
    Sunday -> following Monday."""
    if d.weekday() == 5:
        return d - _dt.timedelta(days=1)
    if d.weekday() == 6:
        return d + _dt.timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def _holidays(year: int) -> frozenset:
    """Full-closure dates for a year, observance applied.

    LATENT-HAZARD NOTE (adversarial verification, Jul 31 2026): a
    Saturday Jan 1 back-shifts to Dec 31 of the PRIOR year, so that date
    would appear in _holidays(year) while belonging to year-1. Today
    that is unreachable — every public entry point resolves membership
    via _holidays(d.year) — but it becomes a real bug the moment anyone
    unions _holidays() across a range (the natural vectorized refactor).
    The result is filtered to the requested year so the hazard cannot
    survive that refactor. NYSE observes no New Year holiday when Jan 1
    falls on a Saturday, which the filter also makes correct by
    construction.
    """
    days = {
        _observed(_dt.date(year, 1, 1)),                     # New Year's Day
        _nth_weekday(year, 1, 0, 3),                          # MLK (from 1998)
        _nth_weekday(year, 2, 0, 3),                          # Washington's Bday
        _easter(year) - _dt.timedelta(days=2),                # Good Friday
        _nth_weekday(year, 5, 0, -1),                         # Memorial Day
        _observed(_dt.date(year, 7, 4)),                      # Independence Day
        _nth_weekday(year, 9, 0, 1),                          # Labor Day
        _nth_weekday(year, 11, 3, 4),                         # Thanksgiving
        _observed(_dt.date(year, 12, 25)),                    # Christmas
    }
    if year >= 2022:                                          # Juneteenth
        days.add(_observed(_dt.date(year, 6, 19)))
    if year < 1998:
        days.discard(_nth_weekday(year, 1, 0, 3))
    return frozenset(d for d in days if d.year == year)


@lru_cache(maxsize=64)
def _half_days(year: int) -> frozenset:
    """Scheduled 13:00 closes, computed from the recurring rules.

    Two are unambiguous and stable: the Friday after Thanksgiving, and
    Christmas Eve when Dec 24 is itself a trading day. July 3 is a half
    day only when it is a trading day AND July 4 falls on a weekday
    (when Jul 4 lands on a weekend, the observance consumes Jul 3 or
    Jul 5 as a full closure instead).
    """
    hol = _holidays(year)
    out = set()

    day_after_thanksgiving = _nth_weekday(year, 11, 3, 4) + _dt.timedelta(days=1)
    if (year >= _DAY_AFTER_THANKSGIVING_FROM
            and day_after_thanksgiving not in hol):
        out.add(day_after_thanksgiving)

    xmas_eve = _dt.date(year, 12, 24)
    if (year >= _CHRISTMAS_EVE_HALF_FROM
            and xmas_eve.weekday() < 5 and xmas_eve not in hol):
        out.add(xmas_eve)

    # July: the early close attaches to the trading day ADJACENT to
    # Independence Day, and which side it lands on changed in 2013.
    # Pre-2013 a Thursday Jul 4 pushed the early close to Friday Jul 5;
    # the "Wednesday before" convention is modern. Getting this wrong
    # makes 2002 incorrect in both directions at once.
    july_3 = _dt.date(year, 7, 3)
    july_4 = _dt.date(year, 7, 4)
    if july_3 not in hol and july_4.weekday() < 5:
        wd = july_3.weekday()
        if wd in (0, 1, 3) and year >= _JULY3_FROM:            # Mon/Tue/Thu
            out.add(july_3)
        elif wd == 2 and year >= _JULY3_WEDNESDAY_FROM:        # Wed
            out.add(july_3)

    july_5 = _dt.date(year, 7, 5)
    lo, hi = _JULY5_FRIDAY_RULE
    if lo <= year <= hi and july_5.weekday() == 4 and july_5 not in hol:
        out.add(july_5)

    return frozenset(out)


# ─── Optional authoritative backend ───────────────────────────────────────

def _load_pmc():
    try:
        import pandas_market_calendars as pmc  # noqa: F401
        return pmc.get_calendar("NYSE")
    except Exception:  # noqa: BLE001
        return None


_PMC = _load_pmc()


def backend_note() -> str:
    """Which calendar is live. Printed by long-window tools so a
    fallback can never masquerade as the authoritative schedule."""
    if _PMC is not None:
        return ("pandas_market_calendars (NYSE) — authoritative schedule "
                 "incl. ad-hoc closures")
    return ("builtin rule engine — recurring holidays computed + "
             f"{len(_ADHOC_CLOSURES)} ad-hoc closures, "
             f"{len(_ADHOC_HALF_DAYS)} ad-hoc early closes, "
             f"{len(_ADHOC_LATE_OPENS)} late opens; verified against the "
             f"NYSE closings record for 2004-2028; supported from "
             f"{EARLIEST_SUPPORTED} (earlier dates raise). Install "
             "pandas_market_calendars for the authoritative schedule")


@lru_cache(maxsize=4096)
def _pmc_schedule(d: _dt.date):
    """(open_ts, close_ts) from pandas_market_calendars, or None when the
    date is not a session."""
    if _PMC is None:
        return None
    try:
        sched = _PMC.schedule(start_date=d.isoformat(), end_date=d.isoformat())
        if len(sched) == 0:
            return None
        row = sched.iloc[0]
        return (row["market_open"].to_pydatetime(),
                 row["market_close"].to_pydatetime())
    except Exception:  # noqa: BLE001
        return None


# ─── Public API ───────────────────────────────────────────────────────────

def _guard_range(d: _dt.date) -> None:
    """The builtin engine refuses dates it would answer wrongly."""
    if _PMC is None and d < EARLIEST_SUPPORTED:
        raise ValueError(
            f"{d} precedes the builtin calendar's supported range "
            f"(from {EARLIEST_SUPPORTED}) — pre-1998 NYSE rules differ "
            "(Uniform Monday Holiday Act, Election Day closures, "
            "Saturday sessions). Install pandas_market_calendars for the "
            "authoritative historical schedule.")


def is_trading_day(d) -> bool:
    d = _as_date(d)
    _guard_range(d)
    if _PMC is not None:
        return _pmc_schedule(d) is not None
    if d.weekday() >= 5:
        return False
    if d in _ADHOC_CLOSURES:
        return False
    return d not in _holidays(d.year)


def is_half_day(d) -> bool:
    d = _as_date(d)
    _guard_range(d)
    if not is_trading_day(d):
        return False
    if _PMC is not None:
        sched = _pmc_schedule(d)
        if sched is None:
            return False
        close_et = sched[1].astimezone(ET)
        return (close_et.hour, close_et.minute) < (16, 0)
    return d in _half_days(d.year) or d in _ADHOC_HALF_DAYS


def session_bounds(d) -> Tuple[_dt.datetime, _dt.datetime]:
    """(open, close) tz-aware for a trading day. Raises on a non-session
    date — callers must gate on is_trading_day first, so a silent
    "midnight to midnight" default can never leak into a time filter."""
    d = _as_date(d)
    if not is_trading_day(d):
        raise ValueError(f"{d} is not an NYSE trading day")
    if _PMC is not None:
        sched = _pmc_schedule(d)
        if sched is not None:
            return sched
    close_t = HALF_DAY_CLOSE if is_half_day(d) else REGULAR_CLOSE
    open_t = _ADHOC_LATE_OPENS.get(d, REGULAR_OPEN)
    return (_dt.datetime.combine(d, open_t, tzinfo=ET),
             _dt.datetime.combine(d, close_t, tzinfo=ET))


def is_market_open(ts: Optional[_dt.datetime] = None) -> bool:
    """Regular-hours only (extended-hours trading is out of scope for
    Module 2's daily sleeves). Close is exclusive: 16:00:00 is shut."""
    ts = _now() if ts is None else _aware(ts)
    d = ts.astimezone(ET).date()
    if not is_trading_day(d):
        return False
    open_ts, close_ts = session_bounds(d)
    return open_ts <= ts < close_ts


def next_open(ts: Optional[_dt.datetime] = None) -> _dt.datetime:
    """The next regular-session open at or after `ts` (today's open if
    we're still before the bell). Scans at most ~2 weeks ahead — long
    enough for any holiday cluster."""
    ts = _now() if ts is None else _aware(ts)
    d = ts.astimezone(ET).date()
    for _ in range(20):
        if is_trading_day(d):
            open_ts, _close = session_bounds(d)
            if ts <= open_ts:
                return open_ts
        d += _dt.timedelta(days=1)
    raise RuntimeError(f"no trading day found within 20 days of {ts}")


def trading_days_between(a, b) -> int:
    """Inclusive count of NYSE sessions in [a, b]."""
    a, b = _as_date(a), _as_date(b)
    if b < a:
        a, b = b, a
    n, d = 0, a
    while d <= b:
        if is_trading_day(d):
            n += 1
        d += _dt.timedelta(days=1)
    return n


def bars_to_years(bars: int, interval: str) -> float:
    """Convert a bar count to calendar years of SESSION time.

    The crypto path divides by 86_400_000 ms/day (24/7); doing that to
    equity bars understates a year by 365/252 = 1.45x and silently
    mis-labels every backtest window's `years` field.
    """
    iv = str(interval).lower()
    if iv not in _INTERVAL_MINUTES:
        raise ValueError(
            f"interval {interval!r} is not valid for equities "
            f"(valid: {', '.join(sorted(_INTERVAL_MINUTES))})")
    minutes = _INTERVAL_MINUTES[iv] * max(0, int(bars))
    return minutes / (REGULAR_SESSION_MINUTES * TRADING_DAYS_PER_YEAR)


# ─── helpers ──────────────────────────────────────────────────────────────

def _as_date(d) -> _dt.date:
    if isinstance(d, _dt.datetime):
        return d.astimezone(ET).date() if d.tzinfo else d.date()
    if isinstance(d, _dt.date):
        return d
    return _dt.date.fromisoformat(str(d)[:10])


def _aware(ts: _dt.datetime) -> _dt.datetime:
    """Naive timestamps are read as ET — the market's own clock — not
    UTC. A naive 09:45 means the market is open, not overnight."""
    return ts.replace(tzinfo=ET) if ts.tzinfo is None else ts


def _now() -> _dt.datetime:
    return _dt.datetime.now(tz=ET)
