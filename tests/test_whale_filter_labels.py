"""Aug 14 2026 — the entry funnel minted a new key every cycle.

First live entry funnel:

  {'signals': 2, 'awaiting_trigger': 1,
   'filter:1D trend up (ema_fast 1883.05 >= ema_slow 1864.59)': 1,
   'filter:persistence': 1}

whale_filters embeds live numeric values in its reason strings, so
`reason.split(":")[0]` (which assumed a "name: detail" shape) kept the
whole string. Those EMA values move every cycle, so each poll creates a
brand-new key and the counts never aggregate — the funnel becomes an
unbounded log of one-offs rather than a distribution you can read.

The reason strings stay verbatim in the journal, where the numbers are
useful. The funnel gets a stable label.

Run: python -m pytest tests/test_whale_filter_labels.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pytest

pytest.importorskip("pandas")

from whale_main import filter_label


# ─── Each gate collapses to one stable name ──────────────────────────────

@pytest.mark.parametrize("reason,expected", [
    ("1D trend up (ema_fast 1883.05 ≥ ema_slow 1864.59)", "multi_tf_trend"),
    ("1D trend down (ema_fast 12.10 ≤ ema_slow 13.44)",   "multi_tf_trend"),
    ("crowded long: funding 0.0412%/8h above +0.0300%",   "funding_crowded"),
    ("crowded short: funding -0.0510%/8h below -0.0300%", "funding_crowded"),
    ("regime gate: strong_up blocks SHORT",               "regime"),
    ("entry trigger: need green 4h close > prior high",   "entry_trigger"),
    ("persistence",                                        "persistence"),
])
def test_reason_maps_to_a_stable_label(reason, expected):
    assert filter_label(reason) == expected


def test_the_same_gate_gives_the_same_label_across_cycles():
    """The bug in one assertion: different numbers, one bucket."""
    a = filter_label("1D trend up (ema_fast 1883.05 ≥ ema_slow 1864.59)")
    b = filter_label("1D trend up (ema_fast 1901.77 ≥ ema_slow 1870.02)")
    assert a == b


def test_an_unrecognized_reason_does_not_leak_numbers():
    """A new filter must not reintroduce unbounded cardinality before
    anyone notices it needs a label."""
    label = filter_label("brand new gate rejected at 1234.56 vs 7890.12")
    assert not any(ch.isdigit() for ch in label)


def test_empty_and_none_are_handled():
    assert filter_label("") == "filter"
    assert filter_label(None) == "filter"


def test_labels_are_bounded_in_length():
    assert len(filter_label("x" * 500)) <= 40


# ─── The funnel uses it ──────────────────────────────────────────────────

def test_run_cycle_labels_filter_rejections():
    import inspect
    import whale_main
    src = inspect.getsource(whale_main.run_cycle)
    assert "filter_label(" in src, \
        "the entry funnel is back to raw reason strings"
    assert 'str(r).split(":")[0]' not in src
