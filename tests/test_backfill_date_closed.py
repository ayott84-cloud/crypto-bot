"""Backfill of the never-stamped date_closed values (Aug 14 2026).

The exact close instant is unrecoverable. These tests pin what IS
knowable — the id-ordered bracket — and that an estimate can never be
written as though it were a record.

Run: python -m pytest tests/test_backfill_date_closed.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from tools.backfill_date_closed import plan_backfill, ESTIMATED_TAG


def _row(i, opened, closed=None, exit_price=1.0):
    return {"id": i, "date_opened": opened, "date_closed": closed,
            "exit_price": exit_price, "strategy": "X"}


def test_open_rows_are_left_alone():
    """No exit_price means the position is still open, not unstamped."""
    assert plan_backfill([_row(1, "2026-08-01T00:00:00",
                                 exit_price=None)]) == []


def test_already_stamped_rows_are_left_alone():
    assert plan_backfill([_row(1, "2026-08-01T00:00:00",
                                 "2026-08-02T00:00:00")]) == []


def test_bracketed_row_takes_the_midpoint():
    rows = [_row(1, "2026-08-01T00:00:00", "2026-08-01T00:00:00"),
            _row(2, "2026-07-01T00:00:00"),                    # unstamped
            _row(3, "2026-08-01T00:00:00", "2026-08-03T00:00:00")]
    (rid, est, basis), = plan_backfill(rows)
    assert rid == 2 and est.startswith("2026-08-02")
    assert basis == "bracketed by neighbours"


def test_trailing_row_takes_the_lower_neighbour():
    rows = [_row(1, "2026-08-01T00:00:00", "2026-08-05T00:00:00"),
            _row(2, "2026-08-02T00:00:00")]
    (rid, est, basis), = plan_backfill(rows)
    assert rid == 2 and est == "2026-08-05T00:00:00"
    assert basis == "lower neighbour"


def test_leading_row_takes_the_upper_neighbour():
    rows = [_row(1, "2026-08-01T00:00:00"),
            _row(2, "2026-08-02T00:00:00", "2026-08-06T00:00:00")]
    (rid, est, basis), = plan_backfill(rows)
    assert rid == 1 and est == "2026-08-06T00:00:00"
    assert basis == "upper neighbour"


def test_no_neighbours_falls_back_to_open_time():
    """A trade cannot close before it opens - a hard lower bound."""
    (rid, est, basis), = plan_backfill([_row(1, "2026-06-17T20:04:19")])
    assert rid == 1 and est == "2026-06-17T20:04:19"
    assert "date_opened" in basis


def test_an_estimate_never_precedes_the_open():
    """Interpolation can land before the trade existed. Writing that
    would put a contradiction in the journal, so it clamps."""
    rows = [_row(1, "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
            _row(2, "2026-05-05T00:00:00"),                    # opened later
            _row(3, "2026-01-01T00:00:00", "2026-01-09T00:00:00")]
    (_rid, est, basis), = plan_backfill(rows)
    assert est == "2026-05-05T00:00:00"
    assert basis == "clamped to date_opened"


def test_every_estimate_is_labelled_as_one():
    assert "estimated" in ESTIMATED_TAG


def test_a_realistic_momentum_run_is_fully_planned():
    """Three AAVE closes with no stamps, interleaved with other bots'
    stamped rows - the actual shape found on the droplet."""
    rows = [
        _row(1, "2026-06-10T00:00:00", "2026-06-15T00:00:00"),   # breakout
        _row(2, "2026-06-17T20:04:19"),                          # AAVE
        _row(3, "2026-07-01T00:00:00", "2026-07-05T00:00:00"),   # scalp
        _row(4, "2026-07-08T00:04:21"),                          # AAVE
        _row(5, "2026-08-10T00:00:00", "2026-08-12T00:00:00"),   # breakout
        _row(6, "2026-08-01T00:05:02"),                          # AAVE
    ]
    plan = plan_backfill(rows)
    assert [p[0] for p in plan] == [2, 4, 6]
    for rid, est, _basis in plan:
        opened = next(r["date_opened"] for r in rows if r["id"] == rid)
        assert est >= opened, f"row {rid} estimated before it opened"
