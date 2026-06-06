"""Phase W.B — whale filter stack tests.

Four pure-function filters that gate signals AFTER classify() produces them:
  - check_multi_tf_trend(direction, df_1d)
  - check_funding_sanity(direction, funding_rate_8h)
  - check_regime_gate(direction, regime_label)
  - check_persistence(coin, direction, persistence_state, min_polls=16)

Each returns (passed: bool, reason: str). Reason is empty when passed.

Run: python -m pytest tests/test_whale_filters.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

import whale_filters


# ─── Multi-TF trend filter ────────────────────────────────────────────────

def test_multi_tf_pass_long_when_1d_uptrend():
    """LONG signal passes when 1D EMA20 > EMA50 on the last bar."""
    df = pd.DataFrame({
        "ema_fast": [100, 101, 102],
        "ema_slow": [ 99,  99,  99],
    })
    ok, _ = whale_filters.check_multi_tf_trend("LONG", df)
    assert ok is True


def test_multi_tf_fail_long_when_1d_downtrend():
    df = pd.DataFrame({
        "ema_fast": [99, 98, 97],
        "ema_slow": [100, 100, 100],
    })
    ok, reason = whale_filters.check_multi_tf_trend("LONG", df)
    assert ok is False
    assert "1D trend" in reason or "down" in reason.lower()


def test_multi_tf_pass_short_when_1d_downtrend():
    df = pd.DataFrame({
        "ema_fast": [99, 98, 97],
        "ema_slow": [100, 100, 100],
    })
    ok, _ = whale_filters.check_multi_tf_trend("SHORT", df)
    assert ok is True


def test_multi_tf_fail_short_when_1d_uptrend():
    df = pd.DataFrame({
        "ema_fast": [101, 102, 103],
        "ema_slow": [ 99,  99,  99],
    })
    ok, reason = whale_filters.check_multi_tf_trend("SHORT", df)
    assert ok is False


def test_multi_tf_pass_when_df_missing_or_empty():
    """No 1D data → pass (don't block on missing data)."""
    ok, _ = whale_filters.check_multi_tf_trend("LONG", None)
    assert ok is True
    ok, _ = whale_filters.check_multi_tf_trend("LONG", pd.DataFrame())
    assert ok is True


# ─── Funding-rate sanity ──────────────────────────────────────────────────

def test_funding_pass_long_when_funding_neutral_or_negative():
    """LONG passes when funding ≤ +0.03%/8h (not crowded)."""
    ok, _ = whale_filters.check_funding_sanity("LONG", funding_rate_8h=0.0001)
    assert ok is True
    ok, _ = whale_filters.check_funding_sanity("LONG", funding_rate_8h=-0.0005)
    assert ok is True


def test_funding_fail_long_when_funding_too_positive():
    """LONG blocked when funding > +0.03%/8h (crowded — whales late)."""
    ok, reason = whale_filters.check_funding_sanity("LONG", funding_rate_8h=0.0005)
    assert ok is False
    assert "crowded" in reason.lower() or "funding" in reason.lower()


def test_funding_fail_short_when_funding_too_negative():
    ok, reason = whale_filters.check_funding_sanity("SHORT", funding_rate_8h=-0.0005)
    assert ok is False


def test_funding_pass_short_when_funding_neutral_or_positive():
    ok, _ = whale_filters.check_funding_sanity("SHORT", funding_rate_8h=0.0001)
    assert ok is True


def test_funding_pass_when_rate_unknown():
    """funding_rate_8h=None → pass (don't block on missing data)."""
    ok, _ = whale_filters.check_funding_sanity("LONG", funding_rate_8h=None)
    assert ok is True


# ─── Regime gate ──────────────────────────────────────────────────────────

def test_regime_pass_long_in_neutral_regimes():
    for label in ("range_low_vol", "range_high_vol", "weak_up", "weak_down", "strong_up"):
        ok, _ = whale_filters.check_regime_gate("LONG", regime_label=label)
        assert ok is True, f"LONG should pass in {label}"


def test_regime_fail_long_in_strong_down():
    """LONG blocked during strong_down regime — fighting the tape."""
    ok, reason = whale_filters.check_regime_gate("LONG", regime_label="strong_down")
    assert ok is False
    assert "strong_down" in reason or "regime" in reason.lower()


def test_regime_fail_short_in_strong_up():
    ok, reason = whale_filters.check_regime_gate("SHORT", regime_label="strong_up")
    assert ok is False


def test_regime_pass_short_in_other_regimes():
    for label in ("range_low_vol", "range_high_vol", "weak_up", "weak_down", "strong_down"):
        ok, _ = whale_filters.check_regime_gate("SHORT", regime_label=label)
        assert ok is True, f"SHORT should pass in {label}"


def test_regime_pass_when_label_unknown():
    ok, _ = whale_filters.check_regime_gate("LONG", regime_label=None)
    assert ok is True
    ok, _ = whale_filters.check_regime_gate("LONG", regime_label="")
    assert ok is True


# ─── Persistence filter ──────────────────────────────────────────────────

def test_persistence_pass_when_signal_held_through_min_polls():
    """A coin/direction that's been signaled in ≥min_polls of the last N polls passes."""
    state = {
        ("BTC", "LONG"): {"poll_count": 17, "last_seen_cycle": 100, "first_cycle": 83},
    }
    ok, _ = whale_filters.check_persistence("BTC", "LONG", state,
                                             current_cycle=100, min_polls=16)
    assert ok is True


def test_persistence_fail_when_signal_too_fresh():
    state = {
        ("BTC", "LONG"): {"poll_count": 4, "last_seen_cycle": 100, "first_cycle": 96},
    }
    ok, reason = whale_filters.check_persistence("BTC", "LONG", state,
                                                  current_cycle=100, min_polls=16)
    assert ok is False
    assert "persistence" in reason.lower() or "poll" in reason.lower()


def test_persistence_fail_when_signal_never_seen():
    """Coin not in state → first poll → block (not persistent yet)."""
    ok, _ = whale_filters.check_persistence("BTC", "LONG",
                                             persistence_state={},
                                             current_cycle=100, min_polls=16)
    assert ok is False


# ─── Filter stack composition ────────────────────────────────────────────

def test_apply_filter_stack_passes_when_all_pass():
    """When all filters return True, the stack passes."""
    df_1d = pd.DataFrame({"ema_fast": [101], "ema_slow": [99]})
    state = {("BTC", "LONG"): {"poll_count": 20, "last_seen_cycle": 100,
                                 "first_cycle": 80}}
    ok, reasons = whale_filters.apply_filter_stack(
        coin="BTC", direction="LONG",
        df_1d=df_1d, funding_rate_8h=0.0001,
        regime_label="weak_up", persistence_state=state,
        current_cycle=100,
    )
    assert ok is True
    assert reasons == []


def test_apply_filter_stack_collects_all_failure_reasons():
    df_1d = pd.DataFrame({"ema_fast": [99], "ema_slow": [100]})  # downtrend
    ok, reasons = whale_filters.apply_filter_stack(
        coin="BTC", direction="LONG",
        df_1d=df_1d, funding_rate_8h=0.001,  # too positive
        regime_label="strong_down",  # against trend
        persistence_state={},  # never seen
        current_cycle=100,
    )
    assert ok is False
    assert len(reasons) == 4
