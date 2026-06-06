"""Phase G — Donchian breakout signal tests.

Per the plan G.1:
  LONG entry:
    - close > Donchian-N upper (default N=20)
    - ATR > ATR_SMA (volatility regime active)
    - ADX > 20 (trend strength)
  SHORT entry (if allow_short=True):
    - close < Donchian-N lower
    - ATR > ATR_SMA
    - ADX > 20
  Exit:
    - 10-bar Donchian opposite-side cross
    - 1.5×ATR adverse move (stop)
    - ADX drops below 15 (trend dying)

Run: python -m pytest tests/test_breakout_signals.py -v
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


# ─── Fixtures ──────────────────────────────────────────────────────────────

def _cfg(**overrides):
    base = {
        "donchian_period":       20,
        "donchian_exit_period":  10,
        "atr_sma_period":        20,
        "adx_period":            14,
        "adx_threshold":         20,
        "adx_exit_threshold":    15,
        "sl_atr_mult":           1.5,   # adverse-move stop per plan G
        "allow_short":           False,
    }
    base.update(overrides)
    return base


def _df_with_columns(**cols):
    """Build a DataFrame; rows padded equal length, NaNs allowed."""
    return pd.DataFrame(cols)


# ─── Donchian channels ─────────────────────────────────────────────────────

def test_compute_donchian_channels_returns_high_low_over_window():
    df = pd.DataFrame({
        "high": [10, 12, 11, 13, 14, 12, 15],
        "low":  [ 5,  6,  7,  6,  8,  7,  9],
    })
    upper, lower = breakout_signals.compute_donchian_channels(df, period=3)
    # Last 3 highs: 14, 12, 15 → max = 15
    # Last 3 lows:  8, 7, 9 → min = 7
    assert upper.iloc[-1] == 15
    assert lower.iloc[-1] == 7


def test_compute_donchian_channels_handles_short_window():
    """Window N=3 with 5 bars → first 2 rows are NaN (incomplete window)."""
    df = pd.DataFrame({"high": [1, 2, 3, 4, 5], "low": [0, 1, 2, 3, 4]})
    upper, lower = breakout_signals.compute_donchian_channels(df, period=3)
    # After period-1 bars, channels become valid
    assert pd.isna(upper.iloc[0])
    assert not pd.isna(upper.iloc[-1])


# ─── LONG entry ────────────────────────────────────────────────────────────

def test_long_entry_when_close_above_donchian_upper_and_filters_pass():
    df = pd.DataFrame({
        # 21 bars (one beyond period) — give the breakout a fresh upper to clear
        "high":    [10] * 20 + [12],
        "low":     [ 5] * 20 + [ 6],
        "close":   [ 8] * 20 + [11],
        "atr":     [ 3] * 21,
        "atr_sma": [ 2] * 21,
        "adx":     [25] * 21,
    })
    sig = breakout_signals.analyze_breakout_entry(df, _cfg())
    assert sig["would_enter"] is True
    assert sig["direction"] == "LONG"


def test_long_entry_blocked_when_close_inside_donchian():
    df = pd.DataFrame({
        "high":    [10] * 21,
        "low":     [ 5] * 21,
        "close":   [ 8] * 21,  # well inside channel
        "atr":     [ 3] * 21,
        "atr_sma": [ 2] * 21,
        "adx":     [25] * 21,
    })
    sig = breakout_signals.analyze_breakout_entry(df, _cfg())
    assert sig["would_enter"] is False
    assert sig["blocked_by"] == "donchian"


def test_long_entry_blocked_when_atr_regime_low():
    df = pd.DataFrame({
        "high":    [10] * 20 + [12],
        "low":     [ 5] * 20 + [ 6],
        "close":   [ 8] * 20 + [11],
        "atr":     [ 1] * 21,
        "atr_sma": [ 3] * 21,
        "adx":     [25] * 21,
    })
    sig = breakout_signals.analyze_breakout_entry(df, _cfg())
    assert sig["would_enter"] is False
    assert sig["blocked_by"] == "atr_regime"


def test_long_entry_blocked_when_adx_below_threshold():
    df = pd.DataFrame({
        "high":    [10] * 20 + [12],
        "low":     [ 5] * 20 + [ 6],
        "close":   [ 8] * 20 + [11],
        "atr":     [ 3] * 21,
        "atr_sma": [ 2] * 21,
        "adx":     [15] * 21,  # weak trend
    })
    sig = breakout_signals.analyze_breakout_entry(df, _cfg())
    assert sig["would_enter"] is False
    assert sig["blocked_by"] == "adx"


# ─── SHORT entry (gated by allow_short) ─────────────────────────────────────

def test_short_entry_when_close_below_donchian_lower_and_filters_pass():
    df = pd.DataFrame({
        "high":    [10] * 20 + [ 9],
        "low":     [ 5] * 20 + [ 3],   # new 20-bar low at last bar
        "close":   [ 8] * 20 + [ 4],   # strictly below prior_lower (=5)
        "atr":     [ 3] * 21,
        "atr_sma": [ 2] * 21,
        "adx":     [25] * 21,
    })
    sig = breakout_signals.analyze_breakout_entry(df, _cfg(allow_short=True))
    assert sig["would_enter"] is True
    assert sig["direction"] == "SHORT"


def test_short_entry_blocked_when_allow_short_false():
    """Even on a textbook SHORT break, gate keeps us flat unless enabled."""
    df = pd.DataFrame({
        "high":    [10] * 20 + [ 9],
        "low":     [ 5] * 20 + [ 3],
        "close":   [ 8] * 20 + [ 4],
        "atr":     [ 3] * 21,
        "atr_sma": [ 2] * 21,
        "adx":     [25] * 21,
    })
    sig = breakout_signals.analyze_breakout_entry(df, _cfg(allow_short=False))
    assert sig["would_enter"] is False


# ─── Exit conditions ───────────────────────────────────────────────────────

def test_long_exit_when_close_below_donchian_lower_exit_band():
    """LONG exits when close crosses below the 10-bar Donchian lower.

    SL distance is set wide (entry 50, ATR 1 → SL at 47.75) so the
    Donchian rule fires alone without SL stealing the trigger.
    """
    df = pd.DataFrame({
        "high":  [55] * 10 + [56],
        "low":   [50] * 10 + [49],
        "close": [52] * 10 + [49],   # below 10-bar lowest low (50)
    })
    reason, kind = breakout_signals.check_breakout_exit(
        df, position_direction="LONG", entry_price=50.0, atr_at_entry=1.0,
        current_adx=22.0, cfg=_cfg())
    assert reason == "Donchian Exit"
    assert kind == "full"


def test_long_exit_when_price_falls_1_5_atr_below_entry():
    """1.5×ATR adverse move triggers SL."""
    df = pd.DataFrame({
        "high":  [12] * 11,
        "low":   [ 9] * 11,
        "close": [10.0] * 10 + [7.0],  # entry 10, fell 3 ($3 = 1.5 * $2 ATR)
    })
    reason, kind = breakout_signals.check_breakout_exit(
        df, position_direction="LONG", entry_price=10.0, atr_at_entry=2.0,
        current_adx=22.0, cfg=_cfg())
    assert reason == "SL Hit"
    assert kind == "full"


def test_long_exit_when_adx_drops_below_exit_threshold():
    """Trend dying — ADX < 15 → exit even if price is fine."""
    df = pd.DataFrame({
        "high":  [12] * 11,
        "low":   [ 9] * 11,
        "close": [10] * 11,
    })
    reason, kind = breakout_signals.check_breakout_exit(
        df, position_direction="LONG", entry_price=10.0, atr_at_entry=2.0,
        current_adx=12.0, cfg=_cfg())
    assert reason == "ADX Exit"
    assert kind == "full"


def test_long_no_exit_when_in_trend_no_adverse_move():
    df = pd.DataFrame({
        "high":  [12, 13, 14, 15, 16, 14, 15, 16, 17, 18, 19],
        "low":   [10, 11, 12, 13, 14, 12, 13, 14, 15, 16, 17],
        "close": [11, 12, 13, 14, 15, 13, 14, 15, 16, 17, 18],
    })
    reason, kind = breakout_signals.check_breakout_exit(
        df, position_direction="LONG", entry_price=11.0, atr_at_entry=2.0,
        current_adx=25.0, cfg=_cfg())
    assert reason is None
    assert kind is None


def test_short_exit_when_close_above_donchian_upper_exit_band():
    """SHORT exits when close crosses above the 10-bar Donchian upper.

    Use wide ATR (5) so SL at entry + 7.5 = 57.5 doesn't pre-empt the
    Donchian rule.
    """
    df = pd.DataFrame({
        "high":  [50] * 10 + [56],
        "low":   [48] * 10 + [52],
        "close": [49] * 10 + [55],   # above 10-bar upper (50)
    })
    reason, kind = breakout_signals.check_breakout_exit(
        df, position_direction="SHORT", entry_price=50.0, atr_at_entry=5.0,
        current_adx=22.0, cfg=_cfg())
    assert reason == "Donchian Exit"
    assert kind == "full"


def test_short_exit_when_price_rises_1_5_atr_above_entry():
    df = pd.DataFrame({
        "high":  [12] * 11,
        "low":   [ 9] * 11,
        "close": [10.0] * 10 + [13.0],  # rose $3 = 1.5 * $2 ATR
    })
    reason, kind = breakout_signals.check_breakout_exit(
        df, position_direction="SHORT", entry_price=10.0, atr_at_entry=2.0,
        current_adx=22.0, cfg=_cfg())
    assert reason == "SL Hit"


# ─── Insufficient data ─────────────────────────────────────────────────────

def test_entry_insufficient_data_when_fewer_than_window_bars():
    df = pd.DataFrame({
        "high": [10, 11], "low": [5, 6], "close": [7, 8],
        "atr": [2, 2], "atr_sma": [1, 1], "adx": [25, 25],
    })
    sig = breakout_signals.analyze_breakout_entry(df, _cfg())
    assert sig["blocked_by"] == "insufficient_data"
    assert sig["would_enter"] is False
