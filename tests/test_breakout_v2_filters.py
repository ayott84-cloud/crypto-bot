"""Phase G.2 — Breakout v2 redesign tests.

Two new gates added to analyze_breakout_entry:
  - Volume confirmation: breakout bar volume > volume_mult × SMA(volume, window)
  - 1D trend gate: requires multi-TF agreement (df_1d supplied by caller)

Plus Turtle-style defaults: donchian_period=55, exit_period=20.

Run: python -m pytest tests/test_breakout_v2_filters.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

import breakout_signals


def _cfg(**overrides):
    base = {
        "donchian_period":       55,
        "donchian_exit_period":  20,
        "atr_sma_period":        20,
        "adx_period":            14,
        "adx_threshold":         20,
        "adx_exit_threshold":    15,
        "sl_atr_mult":           2.5,
        "allow_short":           False,
        # New G.2 gates (opt-in to keep existing tests working)
        "use_volume_filter":     False,
        "volume_threshold_mult": 1.5,
        "volume_sma_period":     20,
        "use_trend_filter":      False,
    }
    base.update(overrides)
    return base


def _breakout_df(volume_at_break: float = 1000):
    """55 quiet bars + 1 breakout bar with configurable volume."""
    n = 55
    df = pd.DataFrame({
        "high":    [10] * n + [12],
        "low":     [ 5] * n + [ 6],
        "close":   [ 8] * n + [11],
        "atr":     [ 3] * (n + 1),
        "atr_sma": [ 2] * (n + 1),
        "adx":     [25] * (n + 1),
        "volume":  [1000] * n + [volume_at_break],
    })
    return df


# ─── Volume filter ────────────────────────────────────────────────────────

def test_long_passes_when_volume_above_threshold():
    df = _breakout_df(volume_at_break=2000)  # 2× the SMA of ~1000
    sig = breakout_signals.analyze_breakout_entry(
        df, _cfg(use_volume_filter=True, volume_threshold_mult=1.5))
    assert sig["would_enter"] is True


def test_long_blocked_when_volume_below_threshold():
    df = _breakout_df(volume_at_break=1000)  # = SMA, fails 1.5× gate
    sig = breakout_signals.analyze_breakout_entry(
        df, _cfg(use_volume_filter=True, volume_threshold_mult=1.5))
    assert sig["would_enter"] is False
    assert sig["blocked_by"] == "volume"


def test_volume_filter_off_doesnt_block():
    df = _breakout_df(volume_at_break=500)  # half the SMA
    sig = breakout_signals.analyze_breakout_entry(
        df, _cfg(use_volume_filter=False))
    assert sig["would_enter"] is True


# ─── 1D trend filter ──────────────────────────────────────────────────────

def test_long_passes_when_1d_ema_fast_above_slow():
    df = _breakout_df()
    df_1d = pd.DataFrame({
        "ema_fast": [101, 102, 103],
        "ema_slow": [ 99,  99,  99],
    })
    sig = breakout_signals.analyze_breakout_entry(
        df, _cfg(use_trend_filter=True), df_1d=df_1d)
    assert sig["would_enter"] is True


def test_long_blocked_when_1d_ema_fast_below_slow():
    df = _breakout_df()
    df_1d = pd.DataFrame({
        "ema_fast": [99, 98, 97],
        "ema_slow": [100, 100, 100],
    })
    sig = breakout_signals.analyze_breakout_entry(
        df, _cfg(use_trend_filter=True), df_1d=df_1d)
    assert sig["would_enter"] is False
    assert sig["blocked_by"] == "trend_1d"


def test_trend_filter_off_doesnt_require_df_1d():
    df = _breakout_df()
    sig = breakout_signals.analyze_breakout_entry(
        df, _cfg(use_trend_filter=False), df_1d=None)
    assert sig["would_enter"] is True


def test_trend_filter_on_passes_when_df_1d_missing():
    """Missing 1D data shouldn't block — default to pass (same shape as whale_filters)."""
    df = _breakout_df()
    sig = breakout_signals.analyze_breakout_entry(
        df, _cfg(use_trend_filter=True), df_1d=None)
    assert sig["would_enter"] is True


# ─── Default config changed to Turtle-style ──────────────────────────────

def test_breakout_assets_default_to_55_20_donchian():
    """Baseline strategies use Turtle System 1 (55/20). Phase K round 3
    added some Turtle System 2 (20/10) variants via the _D20 suffix —
    those are exempt because they're an intentional alternate strategy."""
    from breakout_config import BREAKOUT_ASSETS
    for name, cfg in BREAKOUT_ASSETS.items():
        if name.endswith("_D20"):
            assert cfg["donchian_period"] == 20, f"{name}: D20 entry must be 20"
            assert cfg["donchian_exit_period"] == 10, f"{name}: D20 exit must be 10"
        else:
            assert cfg["donchian_period"] == 55, f"{name}: not Turtle-style 55"
            assert cfg["donchian_exit_period"] == 20, f"{name}: exit not Turtle-style 20"


def test_breakout_assets_have_volume_filter_enabled_by_default():
    from breakout_config import BREAKOUT_ASSETS
    for name, cfg in BREAKOUT_ASSETS.items():
        assert cfg.get("use_volume_filter") is True
        assert cfg.get("use_trend_filter") is True
