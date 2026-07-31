"""Module 2 Phase S0 — annualization convention for equities.

The research pass flagged metrics.py's 365.0 constants as "wrong for
equities." That is only HALF true, and the distinction matters enough
to pin in tests:

  - `annualized_sharpe(pnls, days_observed)` derives trades/year from
    OBSERVED frequency (len(pnls) * 365 / days_observed). When
    days_observed is CALENDAR days that is correct for any asset class
    — a strategy trading every session makes 252 trades per 365
    calendar days and the formula returns 252. No bug.
  - The real hazard is a CALLER CONVENTION MISMATCH: pass a count of
    252 TRADING days as `days_observed` and the 365 constant
    over-annualizes by 1.45x.

So the fix is not to hardcode 252 — it is to make the convention
explicit and selectable. `periods_per_year` defaults to 365.0, leaving
every crypto call site byte-identical.

The genuinely equity-specific conversion (bars -> years) lives in
market_calendar.bars_to_years and is tested there.

Run: python -m pytest tests/test_equity_metrics.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

import metrics


# ─── Backward compatibility: every crypto call site is unchanged ──────────

def test_calmar_default_is_unchanged_365():
    pnls = [10.0, -5.0, 20.0, -3.0]
    assert metrics.calmar(pnls, 1000.0, days=90) == metrics.calmar(
        pnls, 1000.0, days=90, periods_per_year=365.0)


def test_annualized_sharpe_default_is_unchanged_365():
    pnls = [5.0, -2.0, 7.0, -1.0, 3.0]
    assert metrics.annualized_sharpe(pnls, days_observed=90) == \
        metrics.annualized_sharpe(pnls, days_observed=90, periods_per_year=365.0)


# ─── The convention is now explicit and selectable ────────────────────────

def test_calmar_trading_day_convention_scales_correctly():
    """Same window expressed in trading days must annualize the same as
    in calendar days: 252 trading days and 365 calendar days are both
    'one year', so both must yield the same Calmar."""
    pnls = [30.0, -10.0, 25.0, -5.0]
    cal = metrics.calmar(pnls, 1000.0, days=365, periods_per_year=365.0)
    trd = metrics.calmar(pnls, 1000.0, days=252, periods_per_year=252.0)
    assert cal == pytest.approx(trd)


def test_calmar_mismatched_convention_is_the_145x_error():
    """Documents the bug this parameter prevents: feeding a trading-day
    count to the calendar-day default over-annualizes by 365/252."""
    pnls = [30.0, -10.0, 25.0, -5.0]
    correct = metrics.calmar(pnls, 1000.0, days=252, periods_per_year=252.0)
    wrong = metrics.calmar(pnls, 1000.0, days=252)          # 365 default
    assert wrong == pytest.approx(correct * (365.0 / 252.0), rel=1e-3)


def test_annualized_sharpe_trading_day_convention():
    pnls = [4.0, -1.0, 6.0, -2.0, 3.0, 1.0]
    cal = metrics.annualized_sharpe(pnls, days_observed=365,
                                      periods_per_year=365.0)
    trd = metrics.annualized_sharpe(pnls, days_observed=252,
                                      periods_per_year=252.0)
    assert cal == pytest.approx(trd)


def test_annualized_sharpe_equity_daily_series_gives_sqrt252():
    """A daily-bar equity strategy with one observation per session:
    252 observations over 252 trading days annualizes by sqrt(252)."""
    pnls = [1.0, -1.0] * 126                      # 252 observations
    s = metrics.annualized_sharpe(pnls, days_observed=252,
                                    periods_per_year=252.0)
    import math
    import statistics
    expected = (statistics.mean(pnls) / statistics.stdev(pnls)
                 * math.sqrt(252.0))
    assert s == pytest.approx(round(expected, 2), abs=0.05)


def test_periods_per_year_guards_against_zero_and_negative():
    pnls = [1.0, 2.0, -1.0]
    assert metrics.calmar(pnls, 1000.0, days=90, periods_per_year=0) == 0.0
    assert metrics.annualized_sharpe(pnls, 90, periods_per_year=-5) == 0.0
