"""Aug 22 2026 — the decay tracker recorded only signals that traded.

Phase W.2.10 built a cohort-decay alarm to answer one question: are the
whale cohort's directional calls still predictive? It calls
record_signal AFTER open_whale_position, so it only ever saw signals
that survived every gate.

Whale has opened ZERO positions in 30 days. Over 638 cycles it produced
286 signals, of which 228 died at the W.2.13 price-action trigger and
58 at the W.B filter stack. So the tracker recorded nothing, and the one
instrument that could say whether those 286 signals had any edge has
been measuring an empty set for a month.

That matters because the decision in front of us is whether to loosen
the entry trigger. Loosening it is only correct if the signals it
blocks would have made money — and with the tracker downstream of the
trigger, that is precisely the question it cannot answer.

Fourth instance this month of an instrument wired to the wrong point in
the pipeline (whale funnel -> generate_signals the daemon never called;
fill divergence -> state, never the journal; the S2 win rate -> computed
but never printed). The measurement was right each time; the placement
was not.

Signals are now recorded at GENERATION with a `traded` flag, so cohort
accuracy is measurable independently of execution.

Run: python -m pytest tests/test_whale_decay_records_all_signals.py -v
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pytest

pytest.importorskip("pandas")

import whale_decay as wd


# ─── The flag ────────────────────────────────────────────────────────────

def test_record_signal_defaults_to_untraded():
    """Most signals never become trades — that is the whole finding."""
    st = {}
    wd.record_signal(st, "BTC", "LONG", 100.0, 1000)
    assert st["pending"][0]["traded"] is False


def test_record_signal_can_mark_a_traded_signal():
    st = {}
    wd.record_signal(st, "BTC", "LONG", 100.0, 1000, traded=True)
    assert st["pending"][0]["traded"] is True


def test_the_flag_survives_resolution():
    """Accuracy has to be sliceable by traded/untraded after the fact."""
    st = {}
    wd.record_signal(st, "BTC", "LONG", 100.0, 1000, traded=True)
    wd.record_signal(st, "ETH", "SHORT", 50.0, 1000)
    wd.finalize_signals(st, {"BTC": 110.0, "ETH": 40.0}, 1000 + 86_400 + 1)
    flags = {r["coin"]: r["traded"] for r in st["resolved"]}
    assert flags == {"BTC": True, "ETH": False}


# ─── Accuracy over the whole signal set ──────────────────────────────────

def test_accuracy_counts_untraded_signals():
    """The point: 286 blocked signals must still be scoreable."""
    st = {}
    for i, (coin, px) in enumerate([("A", 100.0), ("B", 100.0),
                                      ("C", 100.0), ("D", 100.0)]):
        wd.record_signal(st, coin, "LONG", px, 1000 + i)
    # three right, one wrong — none of them traded
    wd.finalize_signals(st, {"A": 110.0, "B": 110.0, "C": 110.0, "D": 90.0},
                          1000 + 86_400 + 10)
    assert wd.cohort_accuracy_30d(st, 1000 + 86_400 + 20) == pytest.approx(75.0)


def test_accuracy_can_be_restricted_to_traded_signals():
    st = {}
    wd.record_signal(st, "A", "LONG", 100.0, 1000, traded=True)
    wd.record_signal(st, "B", "LONG", 100.0, 1000)
    wd.finalize_signals(st, {"A": 90.0, "B": 110.0}, 1000 + 86_400 + 10)
    now = 1000 + 86_400 + 20
    assert wd.cohort_accuracy_30d(st, now, traded_only=True) == 0.0
    assert wd.cohort_accuracy_30d(st, now) == pytest.approx(50.0)


def test_no_resolved_signals_reports_zero_not_a_crash():
    assert wd.cohort_accuracy_30d({}, 1000) == 0.0


def test_legacy_records_without_the_flag_are_treated_as_traded():
    """Everything recorded before this change WAS a trade, by
    construction — the call site sat after open_whale_position."""
    st = {"resolved": [{"coin": "X", "ts": 1000, "outcome": True}]}
    assert wd.cohort_accuracy_30d(st, 1100, traded_only=True) \
        == pytest.approx(100.0)


# ─── The live path records every signal ──────────────────────────────────

def test_run_cycle_records_signals_before_the_entry_gates():
    """A recorder downstream of the gate cannot measure the gate."""
    import whale_main
    src = inspect.getsource(whale_main.run_cycle)
    rec = src.index("record_signal(")
    gate = src.index("_bump_entry(entry_funnel, \"awaiting_trigger\")")
    assert rec < gate, \
        "record_signal still sits after the entry trigger — it can only " \
        "see signals the trigger already let through"


def test_run_cycle_marks_which_signals_actually_traded():
    """Recording every signal is only half of it — without a way to flag
    the ones that opened, signal edge and execution collapse together."""
    import whale_main
    src = inspect.getsource(whale_main.run_cycle)
    assert "_mark_signal_traded(" in src


def test_marking_flags_the_right_pending_record():
    import whale_main
    st = {"pending": []}
    wd.record_signal(st, "BTC", "LONG", 100.0, 1000)
    wd.record_signal(st, "ETH", "SHORT", 50.0, 1000)
    whale_main._mark_signal_traded(st, "ETH", "SHORT")
    flags = {r["coin"]: r["traded"] for r in st["pending"]}
    assert flags == {"BTC": False, "ETH": True}


def test_marking_an_absent_signal_is_a_no_op():
    """Never raise inside the entry loop for a telemetry concern."""
    import whale_main
    st = {"pending": []}
    whale_main._mark_signal_traded(st, "NOPE", "LONG")   # must not raise
    assert st["pending"] == []
