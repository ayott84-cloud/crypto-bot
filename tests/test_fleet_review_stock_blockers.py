"""Aug 14 2026 — fleet_review was blind to Module 2's entry blockers.

stock_daily_main writes state["stock_signal_status"]; fleet_review's
Entry-blockers panel read state["signal_status"] only. The separate key
is CORRECT — position_manager._TOPLEVEL_BY_BOT namespaces top-level keys
per owner, so a stock daemon writing "signal_status" would clobber the
crypto fleet's rows on the next merge. The defect is the read side.

Consequence at the Aug 14 review: Module 2 had been unpaused since Aug 2
and the operator had no way to see whether its sleeves were evaluating
at all, or why they weren't entering.

Run: python -m pytest tests/test_fleet_review_stock_blockers.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pytest.importorskip("pandas")

from tools.fleet_review import blocked_by_rows, merged_signal_status


def _fresh():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _stale():
    return (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()


# ─── The merge ───────────────────────────────────────────────────────────

def test_merged_status_includes_both_namespaces():
    state = {
        "signal_status": {"BTC_4H": {"checked_at": _fresh(),
                                       "blocked_by": "trend"}},
        "stock_signal_status": {"SPY_REV": {"checked_at": _fresh(),
                                              "blocked_by": "above_sma"}},
    }
    merged = merged_signal_status(state)
    assert set(merged) == {"BTC_4H", "SPY_REV"}


def test_merged_status_survives_either_key_missing():
    assert merged_signal_status({}) == {}
    assert set(merged_signal_status(
        {"stock_signal_status": {"SPY_REV": {"checked_at": _fresh()}}})) \
        == {"SPY_REV"}


def test_merged_status_tolerates_non_dict_values():
    """Watchdog philosophy: a corrupt state file degrades the panel, not
    the review."""
    assert merged_signal_status({"signal_status": None,
                                   "stock_signal_status": "junk"}) == {}


def test_stock_rows_reach_the_blockers_panel():
    state = {
        "signal_status": {
            "BTC_4H": {"checked_at": _fresh(), "blocked_by": "trend"},
            "ETH_4H": {"checked_at": _fresh(), "blocked_by": "trend"},
        },
        "stock_signal_status": {
            "SPY_REV": {"checked_at": _fresh(), "blocked_by": "ibs_high"},
            "QQQ_REV": {"checked_at": _fresh(), "blocked_by": "ibs_high"},
            "EFA_TREND": {"checked_at": _fresh(), "would_enter": True},
        },
    }
    rows = {r["reason"]: r for r in blocked_by_rows(merged_signal_status(state))}
    assert rows["trend"]["n"] == 2
    assert rows["ibs_high"]["n"] == 2
    assert rows["WOULD_ENTER"]["assets"] == ["EFA_TREND"]


def test_stale_stock_rows_are_dropped_like_crypto_ones():
    """A paused-then-forgotten sleeve must not keep reporting relics."""
    state = {"stock_signal_status": {
        "SPY_REV": {"checked_at": _stale(), "blocked_by": "ibs_high"}}}
    assert blocked_by_rows(merged_signal_status(state)) == []


def test_a_silent_module_produces_no_rows_rather_than_wrong_ones():
    """The daemon writes stock_signal_status only AFTER its pause and
    capacity gates, so a paused module legitimately has nothing to show.
    That must read as absence, not as a blocker."""
    assert blocked_by_rows(merged_signal_status(
        {"stock_signal_status": {}})) == []
