"""Exit-reason warnings must respect sample size (Aug 23 2026).

fleet_review printed:

    Momentum   SL Hit:1, Stale Exit:1   ⚠ time>40% — entries into drift?

One stale exit out of two trades trips a 40% threshold. The whole fleet
is held to small-sample discipline — live_vs_backtest refuses a verdict
under 20 trades, the RSI sweep refuses under 100 pooled — and this panel
was exempt, firing diagnostic warnings off n=2.

That matters because these warnings are ACTIONABLE-sounding: "brackets
too tight" and "entries into drift" both invite a config change. Off two
trades, acting on either would be exactly the reflex the rest of this
tooling exists to prevent.

The threshold does not move. The warning simply requires enough trades
for the ratio to mean anything, and says so when it is withheld.

Run: python -m pytest tests/test_exit_reason_warnings.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pytest

pytest.importorskip("pandas")

from tools.fleet_review import EXIT_FLAG_MIN_N, exit_reason_flags


def _counts(**kw):
    return dict(kw)


# ─── Thin samples get no warning ─────────────────────────────────────────

def test_the_observed_case_is_withheld():
    """SL Hit:1, Stale Exit:1 — 50% time-exits on two trades."""
    flags, held = exit_reason_flags({"SL Hit": 1, "Stale Exit": 1})
    assert flags == []
    assert held is True


def test_a_single_trade_never_warns():
    flags, held = exit_reason_flags({"SL Hit": 1})
    assert flags == [] and held is True


def test_an_empty_bot_neither_warns_nor_claims_it_withheld():
    flags, held = exit_reason_flags({})
    assert flags == [] and held is False


# ─── Real samples still warn ─────────────────────────────────────────────

def test_a_heavy_stop_rate_warns_at_sufficient_n():
    counts = {"SL Hit": 8, "Donchian Exit": 2}
    flags, held = exit_reason_flags(counts)
    assert any("SL>60%" in f for f in flags)
    assert held is False


def test_a_heavy_time_exit_rate_warns_at_sufficient_n():
    counts = {"Stale Exit": 6, "SL Hit": 4}
    flags, _held = exit_reason_flags(counts)
    assert any("time>40%" in f for f in flags)


def test_the_two_warnings_are_mutually_exclusive_by_construction():
    """Not a limitation — arithmetic. SL and time exits are disjoint
    categories, so sl/total > 0.6 and tl/total > 0.4 would need the two
    to sum past 1.0. My first draft of this test asserted they could
    fire together; they cannot, and the property is worth pinning so
    nobody later 'fixes' one threshold into overlapping the other."""
    for counts in ({"SL Hit": 7, "Stale Exit": 5, "Donchian Exit": 1},
                    {"SL Hit": 9, "Stale Exit": 6},
                    {"BE Hit": 8, "Time Stop": 7}):
        flags, _ = exit_reason_flags(counts)
        assert len(flags) <= 1, f"{counts} produced {flags}"


def test_a_healthy_mix_warns_about_nothing():
    counts = {"Donchian Exit": 6, "SL Hit": 3, "Stale Exit": 1}
    flags, held = exit_reason_flags(counts)
    assert flags == [] and held is False


# ─── The threshold itself is unchanged ───────────────────────────────────

def test_the_ratios_are_not_relaxed():
    """Only the sample-size requirement is new. 60% and 40% stand —
    withholding a warning is not the same as lowering the bar."""
    counts = {"SL Hit": 6, "Donchian Exit": 4}        # exactly 60%
    flags, _ = exit_reason_flags(counts)
    assert flags == [], "60% is not >60%"
    counts = {"SL Hit": 7, "Donchian Exit": 3}        # 70%
    flags, _ = exit_reason_flags(counts)
    assert any("SL>60%" in f for f in flags)


def test_the_minimum_is_high_enough_to_mean_something():
    assert EXIT_FLAG_MIN_N >= 8


def test_the_report_says_when_it_withheld_a_warning():
    import inspect
    import tools.fleet_review as fr
    src = inspect.getsource(fr.main)
    assert "exit_reason_flags" in src
    assert "n<" in src or "withheld" in src.lower(), \
        "a silently withheld warning reads as a clean bill of health"
