"""Aug 22 2026 — append_forming_bar never once ran in production.

The function exists to shift the decision bar onto D-1 so the sleeve
decides on yesterday's close and fills today, matching the replay. It
calls `_dt.date.today()` when no session is supplied — and `_dt` was
imported ONLY inside _is_rebalance_day's local scope. In production the
call raised NameError, and the function's own bare
`except Exception: return df` swallowed it, so it returned the frame
untouched on every single cycle since Module 2 went live.

The consequence was read as slippage. On Aug 19 QQQ showed signal 729.87
against fill 716.53 (-1.83%) and SPY 772.67 against 769.22 (-0.45%). Not
slippage: the sleeve was deciding on a TWO-day-old close because the
forming bar never appended. The sign flipping between the two is the
tell — drift is directional, a stale reference is not.

Why no test caught it: every existing call in the suite passes
`session=` explicitly, so the default branch — the only one production
uses — was never executed. A default argument that no test exercises is
untested code wearing a passing suite.

Run: python -m pytest tests/test_forming_bar_production_path.py -v
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pytest

pd = pytest.importorskip("pandas")

import stock_daily_main as sdm


def _frame(days, tz="UTC"):
    idx = pd.to_datetime(days)
    if tz:
        idx = idx.tz_localize(tz)
    n = len(days)
    return pd.DataFrame({"open": [1.0] * n, "high": [1.0] * n,
                          "low": [1.0] * n, "close": [1.0] * n,
                          "volume": [10] * n}, index=idx)


# ─── The production path ─────────────────────────────────────────────────

def test_the_default_session_branch_actually_appends():
    """session=None is the ONLY branch production uses, and it was the
    one branch no test ran."""
    df = _frame(["2026-08-17", "2026-08-18"])
    out = sdm.append_forming_bar(df, 3.0)
    assert out is not df, "the default branch silently returned the frame"
    assert len(out) == len(df) + 1


def test_the_default_branch_shifts_the_decision_bar_forward():
    """The whole purpose: iloc[-2] must become yesterday, not two days
    ago. This is the -1.83% 'slippage' in one assertion."""
    today = dt.date.today()
    prior = [str(today - dt.timedelta(days=n)) for n in (3, 2)]
    df = _frame(prior)
    stale = sdm.completed_bar_id(df)
    fresh = sdm.completed_bar_id(sdm.append_forming_bar(df, 3.0))
    assert fresh != stale
    assert fresh == prior[-1]


def test_datetime_is_reachable_at_module_scope():
    """The literal defect: _dt lived inside another function."""
    assert hasattr(sdm, "_dt"), \
        "_dt is not module-level; append_forming_bar will NameError again"


# ─── Existing guarantees still hold ──────────────────────────────────────

def test_a_missing_price_still_no_ops():
    df = _frame(["2026-08-17", "2026-08-18"])
    assert sdm.append_forming_bar(df, None) is df


def test_an_already_published_session_is_not_duplicated():
    today = str(dt.date.today())
    df = _frame(["2026-08-17", today])
    out = sdm.append_forming_bar(df, 3.0)
    assert len(out) == len(df)


def test_a_naive_index_is_handled():
    """build_dataframe returns tz-aware UTC, but a cache fallback may
    not — mixing the two raises inside sort_index and the guard would
    swallow it right back into a stale decision bar."""
    df = _frame(["2026-08-17", "2026-08-18"], tz=None)
    out = sdm.append_forming_bar(df, 3.0)
    assert len(out) == len(df) + 1


def test_an_empty_frame_is_returned_untouched():
    empty = pd.DataFrame({"close": []})
    assert sdm.append_forming_bar(empty, 3.0) is empty


def test_the_appended_row_sorts_last():
    df = _frame(["2026-08-17", "2026-08-18"])
    out = sdm.append_forming_bar(df, 3.0)
    assert list(out.index) == sorted(out.index)
