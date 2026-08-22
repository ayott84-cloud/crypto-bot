"""Excluding defect-produced trades from performance stats (Aug 22 2026).

StockRev's entire record is 15 QQQ round trips booked inside 90 minutes
on Aug 14, when the sleeves re-decided a frozen daily bar every poll.
They report as n=15, WR 66.7%, PF 4.38, +$2.77 — the best numbers in the
fleet — and they measure a poll loop, not a strategy. They will dominate
every 30-day window until mid-September.

Excluding trades from a performance record is the single easiest way to
lie to yourself, so the mechanism is built to make that hard:

  * A trade is excluded only with a WRITTEN REASON stored on the row.
  * Every tool that filters them MUST report the count and the reasons.
    A silent exclusion is indistinguishable from a flattering one.
  * Exclusion never deletes or edits prices, quantities or PnL. The
    fills were real; only their attribution to strategy performance is
    disputed.
  * The tagger is dry-run by default and backs up before writing.

Run: python -m pytest tests/test_trade_exclusion.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pytest

pytest.importorskip("pandas")

from journal import EXCLUDED_PREFIX, excluded_reason, partition_excluded


def _t(notes="", pnl=1.0, bot="StockRev"):
    return {"notes": notes, "net_pnl": pnl, "bot": bot, "result": "WIN"}


# ─── Reading the marker ──────────────────────────────────────────────────

def test_a_plain_trade_is_not_excluded():
    assert excluded_reason(_t()) is None
    assert excluded_reason(_t("Partial close (50%)")) is None


def test_the_reason_is_recoverable():
    t = _t(f"{EXCLUDED_PREFIX} churn loop, frozen-bar defect]")
    assert excluded_reason(t) == "churn loop, frozen-bar defect"


def test_the_marker_survives_surrounding_notes():
    """Tagging appends; it must not require owning the whole field."""
    t = _t(f"Full close after 3 bars. {EXCLUDED_PREFIX} churn loop] extra")
    assert excluded_reason(t) == "churn loop"


def test_missing_or_odd_notes_never_raise():
    for notes in (None, "", 42, [], f"{EXCLUDED_PREFIX} unterminated"):
        excluded_reason({"notes": notes})     # must not raise


def test_an_empty_reason_still_counts_as_excluded():
    """An exclusion without a reason is a bug, but it must not silently
    read as an ordinary trade."""
    assert excluded_reason(_t(f"{EXCLUDED_PREFIX}]")) == ""


# ─── Partitioning ────────────────────────────────────────────────────────

def test_partition_splits_and_keeps_both_sides():
    rows = [_t(), _t(f"{EXCLUDED_PREFIX} churn]"), _t()]
    kept, dropped = partition_excluded(rows)
    assert len(kept) == 2 and len(dropped) == 1


def test_partition_groups_reasons_for_reporting():
    rows = [_t(f"{EXCLUDED_PREFIX} churn loop]"),
            _t(f"{EXCLUDED_PREFIX} churn loop]"),
            _t(f"{EXCLUDED_PREFIX} bad fill]"),
            _t()]
    kept, dropped = partition_excluded(rows)
    from collections import Counter
    reasons = Counter(excluded_reason(t) for t in dropped)
    assert len(kept) == 1
    assert reasons == {"churn loop": 2, "bad fill": 1}


def test_nothing_excluded_returns_an_empty_dropped_list():
    kept, dropped = partition_excluded([_t(), _t()])
    assert len(kept) == 2 and dropped == []


def test_partition_tolerates_an_empty_input():
    assert partition_excluded([]) == ([], [])
    assert partition_excluded(None) == ([], [])


# ─── The honesty properties ──────────────────────────────────────────────

def test_exclusion_does_not_touch_the_numbers():
    """The fills were real. Only their attribution is disputed — a tool
    that rewrote PnL would be falsifying the record, not annotating it."""
    t = _t(f"{EXCLUDED_PREFIX} churn]", pnl=3.41)
    _kept, dropped = partition_excluded([t])
    assert dropped[0]["net_pnl"] == 3.41


def test_the_marker_is_greppable_in_a_raw_journal():
    """An operator reading trades.db by hand must be able to see which
    rows were set aside, without this codebase."""
    assert "EXCLUDED" in EXCLUDED_PREFIX
