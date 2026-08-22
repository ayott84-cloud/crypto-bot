"""Selection + tagging for defect-attributed trades (Aug 22 2026)."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pytest

pytest.importorskip("pandas")

from journal import EXCLUDED_PREFIX, excluded_reason
from tools.exclude_trades import select_rows, tagged_notes


def _r(i, bot="StockRev", sym="QQQ", closed="2026-08-14T20:04:13",
        exit_reason="Above SMA", notes=""):
    return {"id": i, "bot": bot, "symbol": sym, "date_closed": closed,
            "exit_reason": exit_reason, "notes": notes}


# ─── Selection ───────────────────────────────────────────────────────────

def test_selects_by_bot_and_day_and_exit_reason():
    rows = [_r(1), _r(2), _r(3, closed="2026-08-19T19:41:00"),
            _r(4, bot="Breakout"), _r(5, exit_reason="RSI Exit")]
    hits = select_rows(rows, bot="StockRev", on="2026-08-14",
                        exit_reason="Above SMA")
    assert [r["id"] for r in hits] == [1, 2]


def test_already_tagged_rows_are_skipped():
    """Re-running must not stack markers or double-count."""
    rows = [_r(1), _r(2, notes=f"{EXCLUDED_PREFIX} churn]")]
    assert [r["id"] for r in select_rows(rows, bot="StockRev")] == [1]


def test_a_criterion_that_matches_nothing_returns_nothing():
    assert select_rows([_r(1)], bot="Nope") == []


def test_falls_back_to_open_date_when_close_is_absent():
    rows = [{"id": 1, "bot": "StockRev", "symbol": "QQQ",
             "date_closed": None, "date_opened": "2026-08-14T19:59:13",
             "exit_reason": "Above SMA", "notes": ""}]
    assert len(select_rows(rows, on="2026-08-14")) == 1


def test_symbol_narrows_within_a_bot():
    rows = [_r(1, sym="QQQ"), _r(2, sym="SPY")]
    assert [r["id"] for r in select_rows(rows, symbol="SPY")] == [2]


# ─── Tagging ─────────────────────────────────────────────────────────────

def test_tagging_preserves_existing_notes():
    out = tagged_notes("Full close after 3 bars.", "churn loop")
    assert "Full close after 3 bars." in out
    assert excluded_reason({"notes": out}) == "churn loop"


def test_tagging_an_empty_note_is_clean():
    out = tagged_notes("", "churn loop")
    assert out == f"{EXCLUDED_PREFIX} churn loop]"


def test_tagging_handles_a_null_note():
    assert excluded_reason({"notes": tagged_notes(None, "x")}) == "x"


def test_the_round_trip_reads_back_the_exact_reason():
    reason = "churn loop: frozen-bar re-decision, fixed 7bed21e"
    assert excluded_reason({"notes": tagged_notes("", reason)}) == reason
