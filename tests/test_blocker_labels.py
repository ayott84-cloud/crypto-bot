"""Unit tests for blocker_labels — human-friendly diagnostic mapping.

Phase B.2 of the comprehensive enhancement plan. The dashboard previously
rendered the raw `blocked_by` value from signals.py (e.g. "btc_filter",
"trend", "rsi_crossover"). The new module maps each known value to a
sentence an operator can read at a glance.

Run: python -m pytest tests/test_blocker_labels.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

from blocker_labels import BLOCKER_LABELS, blocker_label


# Every value signals.py can emit. Keep this in sync with signals.py
# (the `fail("…")` calls). Drift here = a label is missing.
EXPECTED_KEYS = {
    "insufficient_data",
    "nan_indicators",
    "trend",
    "close_above_ema",
    "atr_regime",
    "rsi_crossover",
    "macd",
    "pmo",
    "volume",
    "mfi",
    "adx",
    "btc_filter",
}


# ─── Coverage: every signal fail() key has a label ──────────────────────────

def test_every_known_blocker_has_a_label():
    """If signals.py adds a new fail() key, this test fails until a label is added."""
    assert EXPECTED_KEYS.issubset(BLOCKER_LABELS.keys()), (
        f"Missing labels for: {EXPECTED_KEYS - BLOCKER_LABELS.keys()}"
    )


def test_no_label_is_empty_or_whitespace():
    for k, v in BLOCKER_LABELS.items():
        assert isinstance(v, str), f"Label for {k!r} is not a string"
        assert v.strip(), f"Label for {k!r} is empty / whitespace"


# ─── btc_filter specifically — the headline reason momentum was silent ──────

def test_btc_filter_label_explains_alt_correlation_gate():
    """The most common label right now — must be operator-readable."""
    label = blocker_label("btc_filter")
    # Both "BTC" and "EMA" should appear so the operator can connect it to
    # the BTC EMA50 chart without needing context.
    assert "BTC" in label
    assert "EMA" in label


# ─── blocker_label() — render path ──────────────────────────────────────────

def test_blocker_label_returns_friendly_for_known_key():
    assert blocker_label("trend") != "trend"  # not just echoing
    assert blocker_label("trend") == BLOCKER_LABELS["trend"]


def test_blocker_label_returns_raw_value_for_unknown_key():
    """Graceful degradation — an unknown key passes through (don't lose info)."""
    assert blocker_label("some_future_key_we_haven't_added_yet") == "some_future_key_we_haven't_added_yet"


def test_blocker_label_handles_none():
    """When the entry isn't blocked (would_enter=True), the value is None — render empty."""
    assert blocker_label(None) == ""


def test_blocker_label_handles_empty_string():
    assert blocker_label("") == ""
