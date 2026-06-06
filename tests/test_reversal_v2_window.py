"""Phase I.2 — Reversal v2: multi-bar conjunction window.

The 1.0 spec required RSI extreme + 3× range + dot polarity on the
SAME bar. Real markets rarely line up that cleanly; 1000-bar backtest
fired 0 trades on all 3 assets.

v2 allows the conjunction within a `window_bars`-bar lookback (default
3) — the capitulation event can span an entry bar + 1-2 follow-throughs.

Run: python -m pytest tests/test_reversal_v2_window.py -v
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
        "oversold":           15.0,
        "overbought":         85.0,
        "range_mult":         2.5,
        "range_sma_length":   14,
        "close_position_pct": 0.30,
        "allow_long":         True,
        "allow_short":        True,
        "atr_length":         14,
        "window_bars":        3,   # NEW in v2
    }
    base.update(overrides)
    return base


def _df_with_late_extreme(n: int = 25):
    """Bar -1: normal. Bar -2: extreme range + close-bottom. Bar -3: normal.
    Cumulative: extreme dot fires 1 bar earlier than the RSI signal."""
    rows = []
    for i in range(n - 3):
        rows.append({"high": 10, "low": 8, "close": 9, "volume": 1000})
    # Bar -3: normal
    rows.append({"high": 10, "low": 8, "close": 9, "volume": 1000})
    # Bar -2: extreme range with close in bottom 10%
    rows.append({"high": 20, "low": 10, "close": 11, "volume": 5000})
    # Bar -1 (latest): normal follow-through close
    rows.append({"high": 12, "low": 11, "close": 11.5, "volume": 1500})
    return pd.DataFrame(rows)


# ─── Window-bar conjunction ──────────────────────────────────────────────

def test_long_fires_when_dot_lagged_by_1_bar_under_window_3():
    """Extreme bar appears at -2 (1 bar before latest); RSI extreme at -1.
    With window_bars=3, both qualify."""
    df = _df_with_late_extreme(25)
    # RSI fixture: prev=8 (oversold), curr=9 (still oversold, rising)
    rsi_vwap = pd.Series([50] * 23 + [8, 9])
    sig = reversal_signals.analyze_reversal_entry(
        df, _cfg(window_bars=3), rsi_vwap_series=rsi_vwap)
    assert sig["would_enter"] is True
    assert sig["direction"] == "LONG"


def test_long_blocked_when_window_bars_is_1_and_dot_lagged():
    """Same data but window_bars=1 → conjunction must be on the SAME bar.
    The extreme dot is at -2, not -1 → no entry."""
    df = _df_with_late_extreme(25)
    rsi_vwap = pd.Series([50] * 23 + [8, 9])
    sig = reversal_signals.analyze_reversal_entry(
        df, _cfg(window_bars=1), rsi_vwap_series=rsi_vwap)
    assert sig["would_enter"] is False


def test_long_blocked_when_extreme_too_old_to_be_in_window():
    """Extreme bar at -5; window=3 → out of window, no entry."""
    rows = []
    for i in range(25):
        rows.append({"high": 10, "low": 8, "close": 9, "volume": 1000})
    rows[-5] = {"high": 20, "low": 10, "close": 11, "volume": 5000}  # extreme
    df = pd.DataFrame(rows)
    rsi_vwap = pd.Series([50] * 23 + [8, 9])
    sig = reversal_signals.analyze_reversal_entry(
        df, _cfg(window_bars=3), rsi_vwap_series=rsi_vwap)
    assert sig["would_enter"] is False


def test_short_fires_when_dot_lagged_by_1_bar_under_window_3():
    """Mirror: extreme range with close in top 90% at -2, RSI overbought at -1."""
    rows = []
    for i in range(22):
        rows.append({"high": 10, "low": 8, "close": 9, "volume": 1000})
    rows.append({"high": 10, "low": 8, "close": 9, "volume": 1000})           # -3
    rows.append({"high": 20, "low": 10, "close": 19, "volume": 5000})         # -2 bearish dot
    rows.append({"high": 12, "low": 11, "close": 11.5, "volume": 1500})       # -1
    df = pd.DataFrame(rows)
    rsi_vwap = pd.Series([50] * 23 + [92, 91])  # overbought, falling
    sig = reversal_signals.analyze_reversal_entry(
        df, _cfg(window_bars=3), rsi_vwap_series=rsi_vwap)
    assert sig["would_enter"] is True
    assert sig["direction"] == "SHORT"


# ─── Backward-compat ─────────────────────────────────────────────────────

def test_window_bars_default_when_unconfigured():
    """If cfg doesn't set window_bars, signature default should still work."""
    df = _df_with_late_extreme(25)
    rsi_vwap = pd.Series([50] * 23 + [8, 9])
    cfg = _cfg()
    del cfg["window_bars"]
    sig = reversal_signals.analyze_reversal_entry(
        df, cfg, rsi_vwap_series=rsi_vwap)
    # Default is 3 → should fire
    assert sig["would_enter"] is True


def test_default_assets_use_daily_and_window_3():
    """I.3: switched to Daily timeframe for true capitulation events."""
    from reversal_config import REVERSAL_ASSETS
    for name, cfg in REVERSAL_ASSETS.items():
        assert cfg["interval"] == "1d", f"{name}: not 1d"
        assert cfg.get("window_bars", 1) >= 3, f"{name}: window_bars not >=3"
