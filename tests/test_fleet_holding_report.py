"""Report holding period, including how much of it is unknown.

Aug 23 2026. holding_hours() landed an hour ago. Adding a measurement
and not wiring it to a reader is the exact failure this week kept
finding — five times, in the whale funnel, the fill divergence, the S2
win rate, the decay tracker and date_opened itself. So it gets wired
in the same commit-run it was created in.

The reporting requirement is specific: EVERY row in the journal today
has an unknown holding period, because the fix only affects positions
closed by the new code. A panel that quietly averaged the measurable
subset would show a confident number computed from nothing. The count
of unknowns is printed alongside, always.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pytest

pytest.importorskip("pandas")

from tools.fleet_review import holding_rows


def _t(bot, hours=None, closed=None):
    c = closed or datetime.now(timezone.utc)
    o = c - timedelta(hours=hours) if hours is not None else c
    return {"bot": bot, "result": "WIN", "net_pnl": 1.0,
            "date_opened": o.isoformat(), "date_closed": c.isoformat()}


# ─── The measurement ─────────────────────────────────────────────────────

def test_median_hold_is_reported_per_bot():
    rows = holding_rows([_t("Breakout", 2), _t("Breakout", 4),
                          _t("Breakout", 6)], days=30)
    assert rows[0]["bot"] == "Breakout"
    assert rows[0]["median_h"] == pytest.approx(4.0)


def test_median_not_mean_so_one_long_hold_does_not_dominate():
    rows = holding_rows([_t("X", 1), _t("X", 2), _t("X", 500)], days=30)
    assert rows[0]["median_h"] == pytest.approx(2.0)


def test_bots_are_reported_separately():
    rows = holding_rows([_t("Breakout", 2), _t("Momentum", 40)], days=30)
    assert {r["bot"] for r in rows} == {"Breakout", "Momentum"}


# ─── The unknowns are never hidden ───────────────────────────────────────

def test_unknown_holds_are_counted_not_dropped():
    """Every pre-Aug-23 row is unknown. Averaging only the measurable
    ones would print a confident number computed from almost nothing."""
    rows = holding_rows([_t("X", 4), _t("X", None), _t("X", None)], days=30)
    assert rows[0]["n"] == 3
    assert rows[0]["unknown"] == 2
    assert rows[0]["median_h"] == pytest.approx(4.0)


def test_an_all_unknown_bot_reports_no_median_rather_than_zero():
    rows = holding_rows([_t("X", None), _t("X", None)], days=30)
    assert rows[0]["median_h"] is None
    assert rows[0]["unknown"] == 2


def test_the_whole_journal_being_unknown_is_representable():
    """Today's actual state — this must not crash or imply a number."""
    rows = holding_rows([_t("A", None), _t("B", None)], days=30)
    assert all(r["median_h"] is None for r in rows)


def test_no_trades_yields_no_rows():
    assert holding_rows([], days=30) == []


def test_rows_outside_the_window_are_excluded():
    old = datetime.now(timezone.utc) - timedelta(days=90)
    rows = holding_rows([_t("X", 4, closed=old)], days=30)
    assert rows == []


# ─── The panel announces what it could not measure ───────────────────────

def test_the_report_prints_the_unknown_count():
    import inspect
    import tools.fleet_review as fr
    src = inspect.getsource(fr.main)
    assert "holding_rows" in src, "holding period is measured but unreported"
    assert "unknown" in src, \
        "the panel would average the measurable subset without saying so"
