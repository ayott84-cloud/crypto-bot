"""Backfill of the never-stamped date_closed values (Aug 14 2026).

The first version of this tool interpolated between id-ordered stamped
neighbours and, on the real journal, collapsed every April/May close
onto 2026-06-05 -- five-week holds on a 4H strategy. The dry run caught
it before anything was written.

The correction: journal.log_trade defaults date_opened to
datetime.now() AT INSERT and no bot passes it, so on a close-only insert
that value IS the close. These tests pin recorded-beats-inferred, and
that a recovered value is never labelled as a guess.

Run: python -m pytest tests/test_backfill_date_closed.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from tools.backfill_date_closed import (
    plan_backfill, is_estimate, ESTIMATED_TAG, RECOVERED_TAG)


def _row(i, opened, closed=None, exit_price=1.0):
    return {"id": i, "date_opened": opened, "date_closed": closed,
            "exit_price": exit_price, "strategy": "X"}


# ─── Rows that need nothing ──────────────────────────────────────────────

def test_open_rows_are_left_alone():
    """No exit_price means still open, not unstamped."""
    assert plan_backfill([_row(1, "2026-08-01T00:00:00",
                                 exit_price=None)]) == []


def test_already_stamped_rows_are_left_alone():
    assert plan_backfill([_row(1, "2026-08-01T00:00:00",
                                 "2026-08-02T00:00:00")]) == []


# ─── Recorded beats inferred ─────────────────────────────────────────────

def test_insert_timestamp_wins_over_interpolation():
    rows = [_row(1, "2026-08-01T00:00:00", "2026-08-01T00:00:00"),
            _row(2, "2026-04-28T12:13:01"),                    # unstamped
            _row(3, "2026-08-01T00:00:00", "2026-08-03T00:00:00")]
    (rid, est, basis), = plan_backfill(rows)
    assert rid == 2 and est == "2026-04-28T12:13:01"
    assert basis == "insert time (close-only row)"
    assert not is_estimate(basis), "a recorded value must not read as a guess"


def test_a_four_hour_strategy_never_gets_a_five_week_hold():
    """The regression that stopped the first --apply: ten rows spanning
    eight days all collapsed onto one June timestamp."""
    rows = [_row(i, f"2026-05-{i:02d}T12:00:00") for i in range(1, 11)]
    rows.append(_row(99, "2026-06-05T00:11:45", "2026-06-05T00:11:45"))
    plan = plan_backfill(rows)
    assert len({est for _r, est, _b in plan}) == 10, \
        "distinct closes collapsed onto a single timestamp"


def test_insert_time_is_clamped_by_a_later_rows_close():
    """A row inserted earlier cannot have closed after a later one."""
    rows = [_row(1, "2026-09-01T00:00:00"),                    # impossible
            _row(2, "2026-08-01T00:00:00", "2026-08-05T00:00:00")]
    (rid, est, basis), = plan_backfill(rows)
    assert rid == 1 and est == "2026-08-05T00:00:00"
    assert basis == "clamped to next stamped close"


# ─── Interpolation, now the narrow fallback ──────────────────────────────

def test_interpolation_survives_only_without_an_insert_time():
    rows = [_row(1, "2026-08-01T00:00:00", "2026-08-01T00:00:00"),
            _row(2, None),
            _row(3, "2026-08-01T00:00:00", "2026-08-03T00:00:00")]
    (rid, est, basis), = plan_backfill(rows)
    assert rid == 2 and est.startswith("2026-08-02")
    assert is_estimate(basis)


def test_lone_row_with_nothing_to_reason_from_is_skipped():
    assert plan_backfill([_row(1, None)]) == []


def test_the_two_tags_say_different_things():
    assert "estimated" in ESTIMATED_TAG
    assert "recovered" in RECOVERED_TAG
    assert ESTIMATED_TAG != RECOVERED_TAG


# ─── The real shape found on the droplet ─────────────────────────────────

def test_a_realistic_momentum_run_uses_its_own_timestamps():
    """The three AAVE closes, interleaved with other bots' stamped rows."""
    rows = [
        _row(1, "2026-06-10T00:00:00", "2026-06-15T00:00:00"),
        _row(2, "2026-06-17T20:04:19"),
        _row(3, "2026-07-01T00:00:00", "2026-07-05T00:00:00"),
        _row(4, "2026-07-08T00:04:21"),
        _row(5, "2026-08-10T00:00:00", "2026-08-12T00:00:00"),
        _row(6, "2026-08-01T00:05:02"),
    ]
    plan = {rid: est for rid, est, _b in plan_backfill(rows)}
    assert plan == {2: "2026-06-17T20:04:19",
                    4: "2026-07-08T00:04:21",
                    6: "2026-08-01T00:05:02"}


def test_no_estimate_ever_precedes_its_own_insert():
    rows = [_row(i, f"2026-05-{i:02d}T12:00:00") for i in range(1, 6)]
    rows.append(_row(50, "2026-07-01T00:00:00", "2026-07-01T00:00:00"))
    for rid, est, _b in plan_backfill(rows):
        opened = next(r["date_opened"] for r in rows if r["id"] == rid)
        assert est >= opened, f"row {rid} dated before it was inserted"
