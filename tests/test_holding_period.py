"""Aug 23 2026 — holding period is unmeasurable for every trade so far.

The +$112.40 ETH breakout close shows opened and closed at the SAME
instant, 2026-08-23T04:51:39. So does every other row. Not a display
bug: journal.log_trade defaults date_opened to datetime.now() at INSERT,
and a close-only insert means both fields land on the close moment.

register_entry has stamped `entry_time` on every position since Phase 0.
No close path has ever passed it. The data existed and never reached the
column that reads it — the same shape as the whale funnel, the fill
divergence, the S2 win rate and the decay tracker.

What it cost: no trade in the journal can answer "how long was this
held". Stale-exit tuning, time-stop validation, and any comparison of
live holding periods against a replay are all currently impossible.

A wrinkle this creates deliberately: the 46 rows repaired by
backfill_date_closed carry date_opened == date_closed, because for those
the insert timestamp WAS the close. Holding period there is not zero, it
is unknown, and holding_hours() returns None rather than 0.0 so a legacy
row cannot masquerade as an instant round trip.

Run: python -m pytest tests/test_holding_period.py -v
"""

from __future__ import annotations

import ast
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pytest.importorskip("pandas")

from journal import holding_hours


def _t(opened, closed):
    return {"date_opened": opened, "date_closed": closed}


# ─── The measurement ─────────────────────────────────────────────────────

def test_a_normal_hold_is_measured():
    o = datetime(2026, 8, 22, 4, 0, tzinfo=timezone.utc)
    c = o + timedelta(hours=6, minutes=30)
    assert holding_hours(_t(o.isoformat(), c.isoformat())) == pytest.approx(6.5)


def test_identical_timestamps_are_unknown_not_zero():
    """The legacy shape. Zero would claim an instant round trip; the
    truth is that nothing recorded when it opened."""
    ts = "2026-08-23T04:51:39"
    assert holding_hours(_t(ts, ts)) is None


def test_a_close_before_an_open_is_unknown():
    """Impossible ordering means the data is wrong, not that the hold
    was negative."""
    assert holding_hours(_t("2026-08-23T10:00:00",
                             "2026-08-23T04:00:00")) is None


def test_missing_fields_are_unknown():
    assert holding_hours(_t(None, "2026-08-23T04:00:00")) is None
    assert holding_hours(_t("2026-08-23T04:00:00", None)) is None
    assert holding_hours({}) is None


def test_unparseable_timestamps_do_not_raise():
    assert holding_hours(_t("not-a-date", "2026-08-23T04:00:00")) is None


def test_mixed_timezone_awareness_is_handled():
    """The journal has both naive and tz-aware ISO strings, because
    date_opened defaulted to a naive now() while close paths pass an
    aware datetime."""
    assert holding_hours(_t("2026-08-22T04:00:00",
                             "2026-08-22T10:00:00+00:00")) \
        == pytest.approx(6.0)


# ─── Every close path must pass the real entry time ──────────────────────

_CLOSE_PATHS = [
    "main.py", "breakout_main.py", "whale_main.py", "funding_main.py",
    "pair_main.py", "stock_daily_main.py", "scalp_main.py",
    "crossover_main.py",
]


def _close_calls(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "log_trade"):
            kw = {k.arg for k in node.keywords}
            if "date_closed" in kw:
                yield node, kw


@pytest.mark.parametrize("module", _CLOSE_PATHS)
def test_close_paths_pass_the_real_entry_time(module):
    path = BOT_DIR / module
    if not path.exists():
        pytest.skip(f"{module} not present")
    for call, kw in _close_calls(path):
        assert "date_opened" in kw, (
            f"{module}:{call.lineno} stamps date_closed but not "
            f"date_opened — the row's holding period will be unmeasurable")
