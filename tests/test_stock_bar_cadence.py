"""Aug 14 2026 — the reversion sleeve churned 15 round trips in 90 min.

The moment the data layer was fixed and QQQ_REV could finally evaluate,
it opened and closed fifteen times inside a single session. On paper
that read as a WIN: n=15, WR 66.7%, PF 4.38, +$2.77. Alpaca paper models
no fees, no slippage and no market impact, so a churn loop is exactly
the shape that environment flatters most.

Three defects compounding:

  1. No new-bar gate. Both analyze_reversion_entry and
     check_reversion_exit read iloc[-2] — the last COMPLETED daily bar —
     which does not change for the whole session. The daemon polls every
     STOCK_POLL_INTERVAL_SECONDS and re-decided an identical bar each
     time: enter (oversold on bar D) -> exit (close(D) > SMA5(D)) ->
     enter again, unbounded. Both can hold on the same bar; a bar can
     close near its own low and still sit above its 5-day mean.

  2. bars_held was read but never incremented anywhere, so max_hold_bars
     was dead code and Time Stop could never fire.

  3. Nothing stopped a position exiting on the bar it entered on.

A daily sleeve must act at most once per completed daily bar. That is
the invariant these tests pin.

Run: python -m pytest tests/test_stock_bar_cadence.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

import stock_daily_main as sdm


def _frame(n=250, end="2026-08-13"):
    idx = pd.bdate_range(end=end, periods=n)
    v = [100.0 + i for i in range(n)]
    return pd.DataFrame({"open": v, "high": v, "low": v, "close": v,
                          "volume": [1e6] * n}, index=idx)


# ─── The bar identity ────────────────────────────────────────────────────

def test_completed_bar_id_is_the_second_to_last_bar():
    """iloc[-2] is what every sleeve reads, so it is what the cadence
    guard must key on. Using the forming bar would reset every poll."""
    df = _frame()
    assert sdm.completed_bar_id(df) == str(df.index[-2].date())


def test_completed_bar_id_is_stable_across_polls_within_a_session():
    df = _frame()
    assert sdm.completed_bar_id(df) == sdm.completed_bar_id(df.copy())


def test_completed_bar_id_advances_when_a_new_bar_completes():
    a = _frame(n=250, end="2026-08-13")
    b = _frame(n=251, end="2026-08-14")
    assert sdm.completed_bar_id(a) != sdm.completed_bar_id(b)


def test_completed_bar_id_is_none_on_an_unusable_frame():
    assert sdm.completed_bar_id(None) is None
    assert sdm.completed_bar_id(_frame(n=1)) is None


# ─── The entry gate ──────────────────────────────────────────────────────

def test_first_evaluation_of_a_bar_is_allowed():
    state = {}
    assert sdm.should_act_on_bar(state, "QQQ_REV", "2026-08-13") is True


def test_second_evaluation_of_the_same_bar_is_refused():
    """The churn loop in one assertion."""
    state = {}
    sdm.should_act_on_bar(state, "QQQ_REV", "2026-08-13")
    sdm.mark_bar_acted(state, "QQQ_REV", "2026-08-13")
    assert sdm.should_act_on_bar(state, "QQQ_REV", "2026-08-13") is False


def test_a_new_bar_reopens_the_gate():
    state = {}
    sdm.mark_bar_acted(state, "QQQ_REV", "2026-08-13")
    assert sdm.should_act_on_bar(state, "QQQ_REV", "2026-08-14") is True


def test_the_gate_is_per_asset():
    state = {}
    sdm.mark_bar_acted(state, "QQQ_REV", "2026-08-13")
    assert sdm.should_act_on_bar(state, "SPY_REV", "2026-08-13") is True


def test_an_unknown_bar_id_refuses_rather_than_churns():
    """No bar id means we cannot prove the bar is new. A daily sleeve
    skipping one cycle costs nothing; churning costs real money."""
    assert sdm.should_act_on_bar({}, "QQQ_REV", None) is False


def test_the_marker_lives_in_the_stock_namespace():
    """position_manager namespaces top-level keys per owner; a key
    outside the stock set would be dropped on the next merge."""
    state = {}
    sdm.mark_bar_acted(state, "QQQ_REV", "2026-08-13")
    from position_manager import _TOPLEVEL_BY_BOT
    assert set(state) <= set(_TOPLEVEL_BY_BOT["stock"]), (
        f"{set(state)} is not owned by the stock namespace and will be "
        f"discarded by _merge_state")


# ─── Holding, and not exiting on the entry bar ───────────────────────────

def test_bars_held_counts_completed_bars_since_entry():
    """It used to be read from a field nothing ever wrote, so
    max_hold_bars never fired."""
    df = _frame(n=250, end="2026-08-13")
    entry_bar = str(df.index[-12].date())
    assert sdm.bars_held_since(df, entry_bar) == 10


def test_bars_held_is_zero_on_the_entry_bar():
    df = _frame()
    assert sdm.bars_held_since(df, sdm.completed_bar_id(df)) == 0


def test_bars_held_is_zero_when_the_entry_bar_is_unknown():
    """Legacy positions carry no entry_bar. Zero is the safe answer:
    it delays a time stop rather than firing one at random."""
    assert sdm.bars_held_since(_frame(), None) == 0


def test_a_position_cannot_exit_on_the_bar_it_entered():
    assert sdm.can_exit_on_bar({"entry_bar": "2026-08-13"},
                                 "2026-08-13") is False
    assert sdm.can_exit_on_bar({"entry_bar": "2026-08-13"},
                                 "2026-08-14") is True


def test_a_legacy_position_without_an_entry_bar_stays_exitable():
    """Positions opened before this change must remain manageable —
    trapping one would be worse than the churn."""
    assert sdm.can_exit_on_bar({}, "2026-08-14") is True
