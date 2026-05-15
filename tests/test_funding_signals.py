"""Unit tests for funding_signals.classify() and helpers.

Run: python -m pytest tests/test_funding_signals.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

from funding_signals import (
    classify,
    trend_allows_fade,
    in_execution_window,
    compute_atr_and_sma,
    compute_ema_and_slope,
    CROWDED_LONG_FADE, CROWDED_SHORT_FADE,
)


# Base cfg used by most tests — easy to override per-test
def _cfg(**overrides):
    base = {
        "pct": 97.0,
        "floor": 0.0005,
        "min_hist": 45,
        "min_oi": 20_000_000,
        "require_low_vol": True,
        "use_trend": True,
    }
    base.update(overrides)
    return base


# Build a 90-entry "history" distribution: median ~0, tails up to +/-0.001
def _normal_history(n: int = 90, scale: float = 0.0002):
    """Pseudo-distribution: evenly spread between -3*scale and +3*scale."""
    return [(-3 + 6 * i / (n - 1)) * scale for i in range(n)]


# ─── Positive cases ──────────────────────────────────────────────────────────

def test_extreme_top_classifies_short():
    """Funding at 99th pctile + > floor → SHORT signal."""
    history = _normal_history()  # spans ~[-0.0006, +0.0006]
    current = 0.0008  # above top of history, definitely >97th pctile + >floor
    sig = classify("BTC", "BTCUSDT", current, history,
                   oi_usd=100_000_000, atr=0.5, atr_sma=1.0, trend_ok=True,
                   cfg=_cfg())
    assert sig is not None
    assert sig.signal == CROWDED_LONG_FADE
    assert sig.direction == "SHORT"
    assert sig.confidence >= 5


def test_extreme_bottom_classifies_long():
    """Funding at 1st pctile + < -floor → LONG signal."""
    history = _normal_history()
    current = -0.0008
    sig = classify("ETH", "ETHUSDT", current, history,
                   oi_usd=100_000_000, atr=0.5, atr_sma=1.0, trend_ok=True,
                   cfg=_cfg())
    assert sig is not None
    assert sig.signal == CROWDED_SHORT_FADE
    assert sig.direction == "LONG"


# ─── Guards ──────────────────────────────────────────────────────────────────

def test_insufficient_history_returns_none():
    sig = classify("BTC", "BTCUSDT", 0.001, [0.0001] * 20,
                   oi_usd=100_000_000, atr=0.5, atr_sma=1.0, trend_ok=True,
                   cfg=_cfg())
    assert sig is None


def test_below_absolute_floor_no_signal():
    """Even at the 99th pctile, if magnitude < floor → no signal (fees would eat it)."""
    history = [v * 0.00001 for v in _normal_history()]   # super-compressed dist
    current = 0.00003  # at 99th pctile but below 0.0005 floor
    sig = classify("X", "XUSDT", current, history,
                   oi_usd=100_000_000, atr=0.5, atr_sma=1.0, trend_ok=True,
                   cfg=_cfg())
    assert sig is None


def test_mid_distribution_no_signal():
    """Funding at the median → no signal."""
    history = _normal_history()
    current = 0.0
    sig = classify("X", "XUSDT", current, history,
                   oi_usd=100_000_000, atr=0.5, atr_sma=1.0, trend_ok=True,
                   cfg=_cfg())
    assert sig is None


def test_oi_filter_vetoes_low_liquidity():
    history = _normal_history()
    sig = classify("X", "XUSDT", 0.001, history,
                   oi_usd=5_000_000,         # below $20M floor
                   atr=0.5, atr_sma=1.0, trend_ok=True, cfg=_cfg())
    assert sig is None


def test_high_vol_regime_vetoes_fade():
    """ATR >= ATR_SMA → high-vol regime → skip fade per the FLIPPED rule."""
    history = _normal_history()
    sig = classify("BTC", "BTCUSDT", 0.001, history,
                   oi_usd=100_000_000,
                   atr=1.5, atr_sma=1.0, trend_ok=True,  # high vol
                   cfg=_cfg())
    assert sig is None


def test_high_vol_regime_allowed_when_filter_off():
    history = _normal_history()
    cfg = _cfg(require_low_vol=False)
    sig = classify("BTC", "BTCUSDT", 0.001, history,
                   oi_usd=100_000_000, atr=1.5, atr_sma=1.0, trend_ok=True, cfg=cfg)
    assert sig is not None  # filter disabled → signal flows through


def test_trend_against_fade_vetoes():
    history = _normal_history()
    sig = classify("BTC", "BTCUSDT", 0.001, history,
                   oi_usd=100_000_000, atr=0.5, atr_sma=1.0,
                   trend_ok=False,  # trend opposes — skip
                   cfg=_cfg())
    assert sig is None


# ─── Trend filter ────────────────────────────────────────────────────────────

def test_trend_filter_short_blocked_when_above_ema_and_up():
    """SHORT fade against price > EMA20 + rising → should be blocked."""
    assert trend_allows_fade("SHORT", last_close=100.0, ema=95.0, slope_sign=1) is False


def test_trend_filter_short_allowed_when_below_ema():
    """SHORT fade with price below EMA → allowed (trend supports the fade)."""
    assert trend_allows_fade("SHORT", last_close=90.0, ema=95.0, slope_sign=-1) is True


def test_trend_filter_long_blocked_when_below_ema_and_down():
    assert trend_allows_fade("LONG", last_close=90.0, ema=95.0, slope_sign=-1) is False


def test_trend_filter_long_allowed_when_above_ema():
    assert trend_allows_fade("LONG", last_close=100.0, ema=95.0, slope_sign=1) is True


def test_trend_filter_neutral_slope_passes():
    """Slope=0 (flat) shouldn't block in either direction."""
    assert trend_allows_fade("SHORT", last_close=100.0, ema=95.0, slope_sign=0) is True
    assert trend_allows_fade("LONG", last_close=90.0, ema=95.0, slope_sign=0) is True


def test_trend_filter_missing_ema_doesnt_block():
    """If EMA can't be computed, don't block — degrade gracefully."""
    assert trend_allows_fade("SHORT", last_close=100.0, ema=None, slope_sign=0) is True


# ─── Execution window ────────────────────────────────────────────────────────

def test_in_window_at_fixing():
    now = datetime(2026, 5, 15, 8, 0, 0, tzinfo=timezone.utc)
    assert in_execution_window(now, window_minutes=30) is True


def test_in_window_just_before_fixing():
    now = datetime(2026, 5, 15, 7, 35, 0, tzinfo=timezone.utc)  # 25 min before 08:00
    assert in_execution_window(now, window_minutes=30) is True


def test_in_window_just_after_fixing():
    now = datetime(2026, 5, 15, 8, 25, 0, tzinfo=timezone.utc)  # 25 min after 08:00
    assert in_execution_window(now, window_minutes=30) is True


def test_outside_window_far_from_fixing():
    now = datetime(2026, 5, 15, 4, 0, 0, tzinfo=timezone.utc)  # 4am UTC — 4h from any fixing
    assert in_execution_window(now, window_minutes=30) is False


# ─── ATR + EMA helpers ───────────────────────────────────────────────────────

def _synthetic_klines(n: int = 50, start: float = 100.0):
    """Return n klines with smooth price action: [t, o, h, l, c, v]."""
    out = []
    p = start
    for i in range(n):
        o = p
        h = p + 1
        l = p - 1
        c = p + (0.5 if i % 2 == 0 else -0.3)
        out.append([1000 + i * 60000, str(o), str(h), str(l), str(c), "1"])
        p = c
    return out


def test_atr_and_sma_returns_floats():
    klines = _synthetic_klines(50)
    atr, sma = compute_atr_and_sma(klines, period=14, sma_period=20)
    assert atr is not None and atr > 0
    assert sma is not None and sma > 0


def test_atr_and_sma_insufficient_data():
    atr, sma = compute_atr_and_sma(_synthetic_klines(10), period=14, sma_period=20)
    assert atr is None or sma is None


def test_ema_and_slope_uptrend():
    """A monotonically rising series produces positive slope."""
    klines = [[i * 60000, "100", "105", "95", str(100 + i), "1"] for i in range(40)]
    ema, slope = compute_ema_and_slope(klines, period=20)
    assert ema is not None
    assert slope == 1


def test_ema_and_slope_downtrend():
    klines = [[i * 60000, "100", "105", "95", str(100 - i * 0.5), "1"] for i in range(40)]
    ema, slope = compute_ema_and_slope(klines, period=20)
    assert ema is not None
    assert slope == -1
