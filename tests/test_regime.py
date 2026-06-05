"""Unit tests for regime.classify_regime_from_values.

Phase B.3a of the comprehensive enhancement plan. Pure-Python helper that
classifies a market into one of six regime labels from the scalar values
of indicators we already compute (EMA20/EMA50/EMA200, ATR, ATR_SMA, ADX).

Six labels:
    strong_up        — trend up + ADX strong
    weak_up          — trend up + ADX weak
    strong_down      — trend down + ADX strong
    weak_down        — trend down + ADX weak
    range_high_vol   — sideways + ATR > ATR_SMA
    range_low_vol    — sideways + ATR < ATR_SMA

Trend direction:
    up    = close > ema20 > ema50 > ema200
    down  = close < ema20 < ema50 < ema200
    flat  = anything else

ADX threshold default 20 (configurable). The wrapper that operates on
a DataFrame lives in regime.py too but is not unit-tested here — it
requires pandas and is exercised by the live bot.

Run: python -m pytest tests/test_regime.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

from regime import classify_regime_from_values


# ─── Trend direction ────────────────────────────────────────────────────────

def test_trend_up_when_close_above_strict_ema_hierarchy():
    r = classify_regime_from_values(
        close=100, ema20=99, ema50=98, ema200=95,
        atr=2.0, atr_sma=1.5, adx=25,
    )
    assert r["trend"] == "up"


def test_trend_down_when_close_below_strict_ema_hierarchy():
    r = classify_regime_from_values(
        close=90, ema20=92, ema50=94, ema200=100,
        atr=2.0, atr_sma=1.5, adx=25,
    )
    assert r["trend"] == "down"


def test_trend_flat_when_emas_cross():
    """EMAs not strictly ordered → flat regardless of ADX."""
    r = classify_regime_from_values(
        close=100, ema20=99, ema50=101, ema200=98,
        atr=2.0, atr_sma=1.5, adx=25,
    )
    assert r["trend"] == "flat"


def test_trend_flat_when_close_below_ema20_but_emas_otherwise_up():
    """Strict definition — close below ema20 invalidates "up" even with good EMA stack."""
    r = classify_regime_from_values(
        close=98, ema20=99, ema50=98, ema200=95,
        atr=2.0, atr_sma=1.5, adx=25,
    )
    assert r["trend"] == "flat"


# ─── Vol regime ─────────────────────────────────────────────────────────────

def test_vol_high_when_atr_above_atr_sma():
    r = classify_regime_from_values(
        close=100, ema20=99, ema50=98, ema200=95,
        atr=2.0, atr_sma=1.5, adx=25,
    )
    assert r["vol"] == "high"


def test_vol_low_when_atr_below_atr_sma():
    r = classify_regime_from_values(
        close=100, ema20=99, ema50=98, ema200=95,
        atr=1.0, atr_sma=1.5, adx=25,
    )
    assert r["vol"] == "low"


# ─── Strength (ADX) ─────────────────────────────────────────────────────────

def test_strength_strong_when_adx_above_threshold():
    r = classify_regime_from_values(
        close=100, ema20=99, ema50=98, ema200=95,
        atr=2.0, atr_sma=1.5, adx=22,
    )
    assert r["strength"] == "strong"


def test_strength_weak_when_adx_below_threshold():
    r = classify_regime_from_values(
        close=100, ema20=99, ema50=98, ema200=95,
        atr=2.0, atr_sma=1.5, adx=15,
    )
    assert r["strength"] == "weak"


def test_adx_threshold_is_configurable():
    """Passing adx_strong_threshold lets the caller change the line."""
    r = classify_regime_from_values(
        close=100, ema20=99, ema50=98, ema200=95,
        atr=2.0, atr_sma=1.5, adx=18, adx_strong_threshold=15,
    )
    assert r["strength"] == "strong"


# ─── Composite labels ───────────────────────────────────────────────────────

def test_label_strong_up():
    r = classify_regime_from_values(
        close=100, ema20=99, ema50=98, ema200=95,
        atr=2.0, atr_sma=1.5, adx=25,
    )
    assert r["label"] == "strong_up"


def test_label_weak_up():
    r = classify_regime_from_values(
        close=100, ema20=99, ema50=98, ema200=95,
        atr=2.0, atr_sma=1.5, adx=15,
    )
    assert r["label"] == "weak_up"


def test_label_strong_down():
    r = classify_regime_from_values(
        close=90, ema20=92, ema50=94, ema200=100,
        atr=2.0, atr_sma=1.5, adx=25,
    )
    assert r["label"] == "strong_down"


def test_label_weak_down():
    r = classify_regime_from_values(
        close=90, ema20=92, ema50=94, ema200=100,
        atr=2.0, atr_sma=1.5, adx=15,
    )
    assert r["label"] == "weak_down"


def test_label_range_high_vol():
    r = classify_regime_from_values(
        close=100, ema20=99, ema50=101, ema200=98,  # flat
        atr=2.0, atr_sma=1.5, adx=15,
    )
    assert r["label"] == "range_high_vol"


def test_label_range_low_vol():
    r = classify_regime_from_values(
        close=100, ema20=99, ema50=101, ema200=98,  # flat
        atr=1.0, atr_sma=1.5, adx=15,
    )
    assert r["label"] == "range_low_vol"


# ─── Missing data ───────────────────────────────────────────────────────────

def test_unknown_label_when_any_input_is_none():
    """If any indicator is missing (None / NaN), classifier returns 'unknown'."""
    r = classify_regime_from_values(
        close=100, ema20=99, ema50=None, ema200=95,
        atr=2.0, atr_sma=1.5, adx=25,
    )
    assert r["label"] == "unknown"
    assert r["trend"] == "unknown"


def test_unknown_when_adx_missing():
    r = classify_regime_from_values(
        close=100, ema20=99, ema50=98, ema200=95,
        atr=2.0, atr_sma=1.5, adx=None,
    )
    assert r["label"] == "unknown"


def test_unknown_when_atr_or_atr_sma_missing():
    r = classify_regime_from_values(
        close=100, ema20=99, ema50=98, ema200=95,
        atr=None, atr_sma=1.5, adx=25,
    )
    assert r["label"] == "unknown"


# ─── Journal-tag helpers ────────────────────────────────────────────────────
# The journal table has btc_trend_at_entry (UP/DOWN/null) and
# atr_regime_at_entry (HIGH/LOW/null) — short codes the dashboard expects.

def test_btc_trend_journal_code_up():
    r = classify_regime_from_values(
        close=100, ema20=99, ema50=98, ema200=95,
        atr=2.0, atr_sma=1.5, adx=25,
    )
    assert r["btc_trend_code"] == "UP"


def test_btc_trend_journal_code_down():
    r = classify_regime_from_values(
        close=90, ema20=92, ema50=94, ema200=100,
        atr=2.0, atr_sma=1.5, adx=25,
    )
    assert r["btc_trend_code"] == "DOWN"


def test_btc_trend_journal_code_none_when_flat():
    r = classify_regime_from_values(
        close=100, ema20=99, ema50=101, ema200=98,
        atr=2.0, atr_sma=1.5, adx=25,
    )
    assert r["btc_trend_code"] is None


def test_atr_regime_journal_code_high():
    r = classify_regime_from_values(
        close=100, ema20=99, ema50=98, ema200=95,
        atr=2.0, atr_sma=1.5, adx=25,
    )
    assert r["atr_regime_code"] == "HIGH"


def test_atr_regime_journal_code_low():
    r = classify_regime_from_values(
        close=100, ema20=99, ema50=98, ema200=95,
        atr=1.0, atr_sma=1.5, adx=25,
    )
    assert r["atr_regime_code"] == "LOW"
