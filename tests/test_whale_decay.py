"""Phase W.C — cohort signal-decay tracker tests.

Rolling-30-day "did the cohort's directional call work?" measure.
Drives a dashboard alarm; below 45% accuracy → auto-pause whale bot.

Public functions:
  record_signal(decay_state, coin, direction, entry_price, cycle_ts)
  score_signal_outcome(coin, direction, entry_price, exit_price) → bool
  finalize_signals(decay_state, current_prices, current_ts, holding_period_s)
  cohort_accuracy_30d(decay_state, now_ts) → float (0-100)
  should_alarm(accuracy_pct, threshold_pct=50) → bool

Run: python -m pytest tests/test_whale_decay.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

import whale_decay


# ─── score_signal_outcome ────────────────────────────────────────────────

def test_score_outcome_long_correct_when_price_rose():
    assert whale_decay.score_signal_outcome("BTC", "LONG", 100, 105) is True


def test_score_outcome_long_incorrect_when_price_fell():
    assert whale_decay.score_signal_outcome("BTC", "LONG", 100, 95) is False


def test_score_outcome_short_correct_when_price_fell():
    assert whale_decay.score_signal_outcome("BTC", "SHORT", 100, 95) is True


def test_score_outcome_short_incorrect_when_price_rose():
    assert whale_decay.score_signal_outcome("BTC", "SHORT", 100, 105) is False


def test_score_outcome_flat_counts_as_incorrect():
    """Exactly flat (no movement) doesn't validate either direction."""
    assert whale_decay.score_signal_outcome("BTC", "LONG", 100, 100) is False
    assert whale_decay.score_signal_outcome("BTC", "SHORT", 100, 100) is False


# ─── record_signal / finalize_signals ────────────────────────────────────

def test_record_signal_adds_pending_entry():
    state = {"pending": [], "resolved": []}
    whale_decay.record_signal(state, "BTC", "LONG", 100.0, 1000)
    assert len(state["pending"]) == 1
    assert state["pending"][0]["coin"] == "BTC"


def test_finalize_resolves_signals_after_holding_period():
    state = {"pending": [
        {"coin": "BTC", "direction": "LONG", "entry_price": 100.0, "ts": 1000},
    ], "resolved": []}
    # Holding period of 86400s (24h) — at ts=87401 we resolve it
    whale_decay.finalize_signals(state,
                                   current_prices={"BTC": 105.0},
                                   current_ts=87401,
                                   holding_period_s=86400)
    assert len(state["pending"]) == 0
    assert len(state["resolved"]) == 1
    assert state["resolved"][0]["outcome"] is True  # LONG, price up


def test_finalize_keeps_signals_within_holding_period():
    state = {"pending": [
        {"coin": "BTC", "direction": "LONG", "entry_price": 100.0, "ts": 1000},
    ], "resolved": []}
    # Only 1 hour elapsed; holding period 24h
    whale_decay.finalize_signals(state,
                                   current_prices={"BTC": 105.0},
                                   current_ts=4600,
                                   holding_period_s=86400)
    assert len(state["pending"]) == 1
    assert len(state["resolved"]) == 0


# ─── cohort_accuracy_30d ─────────────────────────────────────────────────

def test_accuracy_zero_when_no_resolved_signals():
    state = {"pending": [], "resolved": []}
    assert whale_decay.cohort_accuracy_30d(state, now_ts=1000) == 0.0


def test_accuracy_uses_only_last_30_days():
    """A 6-month-old win doesn't count toward 30-day accuracy."""
    state = {"pending": [], "resolved": [
        {"coin": "BTC", "ts": 1000, "outcome": True},   # 6 months ago
        {"coin": "BTC", "ts": 100_000_000, "outcome": False},
        {"coin": "BTC", "ts": 100_001_000, "outcome": True},
    ]}
    # now is around 100,002,000; 30 days = 2,592,000s
    now = 100_002_000
    acc = whale_decay.cohort_accuracy_30d(state, now_ts=now)
    # only last 2 resolved count: 1 win / 2 = 50%
    assert acc == pytest.approx(50.0)


def test_accuracy_handles_all_wins():
    state = {"pending": [], "resolved": [
        {"coin": "BTC", "ts": 100_000_000, "outcome": True},
        {"coin": "ETH", "ts": 100_001_000, "outcome": True},
    ]}
    assert whale_decay.cohort_accuracy_30d(state, now_ts=100_002_000) == 100.0


# ─── should_alarm ────────────────────────────────────────────────────────

def test_should_alarm_below_threshold():
    assert whale_decay.should_alarm(40.0, threshold_pct=50.0) is True


def test_should_alarm_at_or_above_threshold():
    assert whale_decay.should_alarm(50.0, threshold_pct=50.0) is False
    assert whale_decay.should_alarm(65.0, threshold_pct=50.0) is False


# ─── load_state / save_state round trip ──────────────────────────────────

def test_decay_state_round_trips_through_json(tmp_path):
    path = tmp_path / "decay.json"
    state = {
        "pending":  [{"coin": "BTC", "direction": "LONG", "entry_price": 100.0, "ts": 1000}],
        "resolved": [{"coin": "ETH", "ts": 1500, "outcome": True}],
    }
    whale_decay.save_decay_state(state, path)
    loaded = whale_decay.load_decay_state(path)
    assert loaded["pending"][0]["coin"] == "BTC"
    assert loaded["resolved"][0]["outcome"] is True


def test_decay_state_empty_on_missing_file(tmp_path):
    path = tmp_path / "missing.json"
    loaded = whale_decay.load_decay_state(path)
    assert loaded == {"pending": [], "resolved": []}
