"""Phase I.1 — RSI-VWAP Extreme Reversal signal tests.

Spec extracted from Alex Carter Trading's Whop product (screenshots
provided by operator). Two indicators combined; both must fire same bar:

1. RSI VWAP (length 15)
   - RSI computed on session-anchored VWAP instead of close
   - Oversold 10 / Overbought 90
   - "Cloud" color: green = rising vs prior bar, red = falling

2. Extreme Reversal Setup (multiplier 3)
   - Bar range >= 3 × SMA(range, 14)
   - Bullish dot (BELOW candle): close in lower 30% of bar range
   - Bearish dot (ABOVE candle): close in upper 30% of bar range

Entry rules:
  LONG  = RSI(VWAP) < 10 AND rising AND bullish reversal dot — same bar
  SHORT = RSI(VWAP) > 90 AND falling AND bearish reversal dot — same bar

Run: python -m pytest tests/test_reversal_signals.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

import reversal_signals


def _cfg(**overrides):
    base = {
        "rsi_length":         15,
        "oversold":           10.0,
        "overbought":         90.0,
        "range_mult":         3.0,
        "range_sma_length":   14,
        "close_position_pct": 0.30,  # close in bottom/top 30% of range
        "allow_long":         True,
        "allow_short":        True,
        "atr_length":         14,
    }
    base.update(overrides)
    return base


# ─── compute_vwap ──────────────────────────────────────────────────────────

def test_vwap_simple_case_returns_volume_weighted_average():
    df = pd.DataFrame({
        "high":   [10, 11, 12],
        "low":    [ 8,  9, 10],
        "close":  [ 9, 10, 11],
        "volume": [100, 200, 300],
    })
    vwap = reversal_signals.compute_vwap(df)
    # Typical price = (h+l+c)/3 for each bar: 9, 10, 11
    # Cumulative TP*V: 900, 900+2000=2900, 2900+3300=6200
    # Cumulative V:    100, 300,           600
    # VWAP:            9.0, 9.667,         10.333
    assert vwap.iloc[0] == pytest.approx(9.0)
    assert vwap.iloc[-1] == pytest.approx(10.333, rel=0.01)


def test_vwap_handles_zero_volume_safely():
    df = pd.DataFrame({
        "high":   [10, 11, 12],
        "low":    [ 8,  9, 10],
        "close":  [ 9, 10, 11],
        "volume": [0,   0,  0],
    })
    vwap = reversal_signals.compute_vwap(df)
    # All NaN or all close-equivalents are acceptable; no crash
    assert len(vwap) == 3


# ─── compute_rsi_vwap ─────────────────────────────────────────────────────

def test_rsi_vwap_returns_series_of_same_length():
    df = pd.DataFrame({
        "high":   [10] * 20,
        "low":    [ 5] * 20,
        "close":  [ 7] * 20,
        "volume": [100] * 20,
    })
    rsi = reversal_signals.compute_rsi_vwap(df, length=15)
    assert len(rsi) == 20
    # On a perfectly flat series RSI is undefined → NaN for early bars
    assert pd.isna(rsi.iloc[0])


def test_rsi_vwap_rising_when_vwap_trends_up():
    """A monotonically rising VWAP should pull RSI(VWAP) toward 100."""
    df = pd.DataFrame({
        "high":   list(range(10, 40)),
        "low":    list(range(8,  38)),
        "close":  list(range(9,  39)),
        "volume": [100] * 30,
    })
    rsi = reversal_signals.compute_rsi_vwap(df, length=15)
    # Last value should be high (rising series → RSI near 100)
    assert rsi.iloc[-1] > 70


# ─── Extreme Reversal Setup ───────────────────────────────────────────────

def test_extreme_bar_detected_when_range_ge_3x_avg():
    df = pd.DataFrame({
        # 14 normal bars (range ~2) + 1 extreme bar (range 9)
        "high":  [10] * 14 + [20],
        "low":   [ 8] * 14 + [11],
        "close": [ 9] * 14 + [12],   # close in lower 11% of [11,20]
    })
    is_extreme = reversal_signals.is_extreme_bar(df, range_mult=3.0,
                                                  range_sma_length=14)
    assert is_extreme is True


def test_extreme_bar_not_detected_for_normal_range():
    df = pd.DataFrame({
        "high":  [10] * 14 + [11],
        "low":   [ 8] * 14 + [ 9],
        "close": [ 9] * 14 + [10],
    })
    assert reversal_signals.is_extreme_bar(df, range_mult=3.0,
                                            range_sma_length=14) is False


# ─── Dot polarity ─────────────────────────────────────────────────────────

def test_bullish_dot_when_close_in_lower_30_pct_of_range():
    df = pd.DataFrame({
        "high":  [10] * 14 + [20],
        "low":   [ 8] * 14 + [10],
        "close": [ 9] * 14 + [11],  # close at 11; range [10,20]; pos = 0.1 = 10%
    })
    polarity = reversal_signals.reversal_dot_polarity(df, close_position_pct=0.30)
    assert polarity == "bullish"


def test_bearish_dot_when_close_in_upper_30_pct_of_range():
    df = pd.DataFrame({
        "high":  [10] * 14 + [20],
        "low":   [ 8] * 14 + [10],
        "close": [ 9] * 14 + [18],  # close at 18; range [10,20]; pos = 0.8 = 80%
    })
    polarity = reversal_signals.reversal_dot_polarity(df, close_position_pct=0.30)
    assert polarity == "bearish"


def test_no_dot_when_close_in_middle_of_range():
    df = pd.DataFrame({
        "high":  [10] * 14 + [20],
        "low":   [ 8] * 14 + [10],
        "close": [ 9] * 14 + [15],  # close mid-range = 50%
    })
    polarity = reversal_signals.reversal_dot_polarity(df, close_position_pct=0.30)
    assert polarity is None


# ─── analyze_reversal_entry ───────────────────────────────────────────────

def test_long_entry_when_all_three_long_conditions_align():
    """RSI(VWAP) < 10 + rising + bullish reversal dot → LONG."""
    # Constructed: 18 normal bars + 1 extreme bar with close in lower 10%
    # of range, and a contrived VWAP series that lands RSI<10 + rising.
    # Easiest path: directly inject the rsi_vwap series.
    df = pd.DataFrame({
        "high":   [10] * 18 + [20],
        "low":    [ 8] * 18 + [10],
        "close":  [ 9] * 18 + [11],
        "volume": [100] * 19,
    })
    # Manually override rsi_vwap so the test isn't dependent on real VWAP math
    rsi_vwap = pd.Series([50] * 17 + [8, 9])  # prev=8 < 10, curr=9 > prev (rising)
    sig = reversal_signals.analyze_reversal_entry(df, _cfg(),
                                                   rsi_vwap_series=rsi_vwap)
    assert sig["would_enter"] is True
    assert sig["direction"] == "LONG"


def test_short_entry_when_all_three_short_conditions_align():
    df = pd.DataFrame({
        "high":   [10] * 18 + [20],
        "low":    [ 8] * 18 + [10],
        "close":  [ 9] * 18 + [18],  # close in upper 80% → bearish dot
        "volume": [100] * 19,
    })
    rsi_vwap = pd.Series([50] * 17 + [92, 91])  # prev=92 > 90, curr=91 < prev (falling)
    sig = reversal_signals.analyze_reversal_entry(df, _cfg(),
                                                   rsi_vwap_series=rsi_vwap)
    assert sig["would_enter"] is True
    assert sig["direction"] == "SHORT"


def test_long_blocked_when_no_extreme_bar():
    """RSI extreme but no 3× range bar → no entry."""
    df = pd.DataFrame({
        "high":   [10] * 19,
        "low":    [ 8] * 19,
        "close":  [ 9] * 19,
        "volume": [100] * 19,
    })
    rsi_vwap = pd.Series([50] * 17 + [8, 9])
    sig = reversal_signals.analyze_reversal_entry(df, _cfg(),
                                                   rsi_vwap_series=rsi_vwap)
    assert sig["would_enter"] is False
    assert sig["blocked_by"] == "no_extreme_bar"


def test_long_blocked_when_dot_polarity_wrong():
    """RSI says LONG but dot is bearish (close at top of range) → block."""
    df = pd.DataFrame({
        "high":   [10] * 18 + [20],
        "low":    [ 8] * 18 + [10],
        "close":  [ 9] * 18 + [19],  # bearish dot
        "volume": [100] * 19,
    })
    rsi_vwap = pd.Series([50] * 17 + [8, 9])  # LONG-side RSI
    sig = reversal_signals.analyze_reversal_entry(df, _cfg(),
                                                   rsi_vwap_series=rsi_vwap)
    assert sig["would_enter"] is False
    assert sig["blocked_by"] == "wrong_dot"


def test_long_blocked_when_rsi_not_in_oversold():
    df = pd.DataFrame({
        "high":   [10] * 18 + [20],
        "low":    [ 8] * 18 + [10],
        "close":  [ 9] * 18 + [11],
        "volume": [100] * 19,
    })
    rsi_vwap = pd.Series([50] * 17 + [25, 28])  # not below 10
    sig = reversal_signals.analyze_reversal_entry(df, _cfg(),
                                                   rsi_vwap_series=rsi_vwap)
    assert sig["would_enter"] is False
    assert sig["blocked_by"] == "rsi_not_extreme"


def test_long_blocked_when_rsi_falling_not_rising():
    """RSI is below 10 but FALLING (red cloud) — wrong cloud → block."""
    df = pd.DataFrame({
        "high":   [10] * 18 + [20],
        "low":    [ 8] * 18 + [10],
        "close":  [ 9] * 18 + [11],
        "volume": [100] * 19,
    })
    rsi_vwap = pd.Series([50] * 17 + [9, 7])  # 9 → 7 = falling
    sig = reversal_signals.analyze_reversal_entry(df, _cfg(),
                                                   rsi_vwap_series=rsi_vwap)
    assert sig["would_enter"] is False
    assert sig["blocked_by"] == "wrong_cloud"


def test_short_blocked_when_allow_short_false():
    df = pd.DataFrame({
        "high":   [10] * 18 + [20],
        "low":    [ 8] * 18 + [10],
        "close":  [ 9] * 18 + [18],
        "volume": [100] * 19,
    })
    rsi_vwap = pd.Series([50] * 17 + [92, 91])
    sig = reversal_signals.analyze_reversal_entry(df, _cfg(allow_short=False),
                                                   rsi_vwap_series=rsi_vwap)
    assert sig["would_enter"] is False


def test_long_blocked_when_allow_long_false():
    df = pd.DataFrame({
        "high":   [10] * 18 + [20],
        "low":    [ 8] * 18 + [10],
        "close":  [ 9] * 18 + [11],
        "volume": [100] * 19,
    })
    rsi_vwap = pd.Series([50] * 17 + [8, 9])
    sig = reversal_signals.analyze_reversal_entry(df, _cfg(allow_long=False),
                                                   rsi_vwap_series=rsi_vwap)
    assert sig["would_enter"] is False


def test_entry_blocked_when_insufficient_history():
    df = pd.DataFrame({
        "high":   [10, 11],
        "low":    [ 8,  9],
        "close":  [ 9, 10],
        "volume": [100, 100],
    })
    sig = reversal_signals.analyze_reversal_entry(df, _cfg())
    assert sig["would_enter"] is False
    assert sig["blocked_by"] == "insufficient_data"
