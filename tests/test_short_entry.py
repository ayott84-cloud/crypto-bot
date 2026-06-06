"""Phase E.1 — Momentum SHORT signal mirror.

analyze_short_entry_signal() mirrors analyze_entry_signal() with each
condition inverted:

| Filter           | LONG                          | SHORT                       |
|------------------|-------------------------------|-----------------------------|
| trend            | ema_fast > ema_slow           | ema_fast < ema_slow         |
| close_above_ema  | close > ema_*                 | close < ema_*               |
| atr_regime       | atr > atr_sma  (vol gate)     | atr > atr_sma  (same)       |
| rsi_crossover    | RSI crosses ABOVE sma, 50-70  | RSI crosses BELOW sma, 30-50|
| macd             | hist > 0 (or line>signal)     | hist < 0 (or line<signal)   |
| pmo              | pmo > pmo_signal              | pmo < pmo_signal            |
| mfi              | mfi > 50                      | mfi < 50                    |
| adx              | adx > 20  (trend strength)    | same                        |
| btc_filter       | btc_close > btc_ema           | btc_close < btc_ema         |

Run: python -m pytest tests/test_short_entry.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

import signals


# ─── Fixtures ──────────────────────────────────────────────────────────────

def _cfg(**overrides):
    """Reasonable LONG/SHORT-shared defaults; SHORT-specific keys can override."""
    base = {
        "ema_fast": 20, "ema_slow": 50,
        "close_above": "ema_fast",
        "rsi_min": 50, "rsi_max": 70,
        "rsi_min_short": 30, "rsi_max_short": 50,
        "macd_mode": "strict",
        "use_pmo": False, "use_volume_filter": False,
        "use_mfi_filter": False, "use_adx_filter": False,
        "use_btc_filter": False,
    }
    base.update(overrides)
    return base


def _df_ideal_short(**values):
    """Build a 3-bar DataFrame where every SHORT filter passes by default.

    Signal logic reads curr = df.iloc[-2] (row 1) and prev = df.iloc[-3]
    (row 0). Row 2 is unused. So all setup conditions land on rows 0+1.
    """
    defaults = {
        #                   prev(0)  curr(1)  unused(2)
        "close":            [100,    95,      90],   # curr close BELOW emas
        "ema_fast":         [99,     98,      97],   # downtrend
        "ema_slow":         [100,    100,     100],  # ema_fast < ema_slow always
        "atr":              [3,      3,       3],
        "atr_sma":          [2,      2,       2],    # atr > atr_sma (high vol)
        "rsi":              [55,     40,      35],   # crosses DOWN through SMA at curr, in 30-50
        "rsi_sma":          [50,     45,      40],   # prev: 55 >= 50, curr: 40 < 45 = cross DOWN
        "macd_hist":        [0,      -0.5,    -1],   # hist < 0 at curr
        "macd_line":        [0,      -1,      -2],
        "macd_signal_line": [0,      0,       0],
        "pmo":              [0,      -1,      -2],   # pmo < signal at curr
        "pmo_signal":       [0,      0,       0],
        "vol_sma":          [100,    100,     100],
        "volume":           [120,    120,     120],  # volume > 0.8 * sma
        "mfi":              [50,     40,      30],   # mfi < 50 at curr
        "adx":              [25,     25,      25],
    }
    defaults.update(values)
    return pd.DataFrame(defaults)


# ─── Trend filter ──────────────────────────────────────────────────────────

def test_short_signal_passes_trend_when_ema_fast_below_slow():
    df = _df_ideal_short()
    result = signals.analyze_short_entry_signal(df, _cfg())
    assert result["filters"]["trend"] is True


def test_short_signal_fails_trend_when_ema_fast_above_slow():
    """In an uptrend (ema_fast > ema_slow), the SHORT signal must block."""
    df = _df_ideal_short(ema_fast=[101, 102, 103], ema_slow=[99, 99, 99])
    result = signals.analyze_short_entry_signal(df, _cfg())
    assert result["filters"]["trend"] is False
    assert result["blocked_by"] == "trend"
    assert result["would_enter"] is False


# ─── Close-below-EMA filter ────────────────────────────────────────────────

def test_short_signal_passes_when_close_below_ema_fast():
    df = _df_ideal_short()
    result = signals.analyze_short_entry_signal(df, _cfg())
    assert result["filters"]["close_above_ema"] is True  # name reused; "True" = SHORT-side OK


def test_short_signal_fails_when_close_above_ema_fast():
    df = _df_ideal_short(close=[100, 100, 110])
    result = signals.analyze_short_entry_signal(df, _cfg())
    assert result["filters"]["close_above_ema"] is False


# ─── RSI crossover (inverted band) ─────────────────────────────────────────

def test_short_signal_passes_rsi_cross_down_in_30_50_band():
    df = _df_ideal_short()
    result = signals.analyze_short_entry_signal(df, _cfg())
    assert result["filters"]["rsi_crossover"] is True


def test_short_signal_fails_rsi_when_above_50():
    """RSI must be in the 30-50 range for SHORT (downside momentum band)."""
    df = _df_ideal_short(rsi=[80, 75, 65], rsi_sma=[60, 60, 70])
    result = signals.analyze_short_entry_signal(df, _cfg())
    assert result["filters"]["rsi_crossover"] is False


def test_short_signal_fails_when_rsi_crosses_up_not_down():
    df = _df_ideal_short(rsi=[30, 35, 45], rsi_sma=[45, 45, 40])  # crossing UP
    result = signals.analyze_short_entry_signal(df, _cfg())
    assert result["filters"]["rsi_crossover"] is False


# ─── MACD inverted ─────────────────────────────────────────────────────────

def test_short_signal_passes_macd_when_hist_negative_strict():
    df = _df_ideal_short()
    result = signals.analyze_short_entry_signal(df, _cfg())
    assert result["filters"]["macd"] is True


def test_short_signal_fails_macd_when_hist_positive_strict():
    df = _df_ideal_short(macd_hist=[0, 0, 0.5])
    result = signals.analyze_short_entry_signal(df, _cfg())
    assert result["filters"]["macd"] is False


# ─── PMO inverted ──────────────────────────────────────────────────────────

def test_short_signal_passes_pmo_when_pmo_below_signal():
    df = _df_ideal_short()
    cfg = _cfg(use_pmo=True)
    result = signals.analyze_short_entry_signal(df, cfg)
    assert result["filters"]["pmo"] is True


def test_short_signal_fails_pmo_when_pmo_above_signal():
    df = _df_ideal_short(pmo=[0, 0, 1], pmo_signal=[0, 0, 0])
    cfg = _cfg(use_pmo=True)
    result = signals.analyze_short_entry_signal(df, cfg)
    assert result["filters"]["pmo"] is False


# ─── MFI inverted ──────────────────────────────────────────────────────────

def test_short_signal_passes_mfi_when_below_threshold():
    df = _df_ideal_short()
    cfg = _cfg(use_mfi_filter=True, mfi_threshold=50)
    result = signals.analyze_short_entry_signal(df, cfg)
    assert result["filters"]["mfi"] is True


def test_short_signal_fails_mfi_when_above_threshold():
    df = _df_ideal_short(mfi=[60, 60, 70])
    cfg = _cfg(use_mfi_filter=True, mfi_threshold=50)
    result = signals.analyze_short_entry_signal(df, cfg)
    assert result["filters"]["mfi"] is False


# ─── ADX direction-agnostic ────────────────────────────────────────────────

def test_short_signal_passes_adx_when_strong_trend():
    df = _df_ideal_short()
    cfg = _cfg(use_adx_filter=True, adx_threshold=20)
    result = signals.analyze_short_entry_signal(df, cfg)
    assert result["filters"]["adx"] is True


def test_short_signal_fails_adx_when_weak_trend():
    df = _df_ideal_short(adx=[10, 10, 12])
    cfg = _cfg(use_adx_filter=True, adx_threshold=20)
    result = signals.analyze_short_entry_signal(df, cfg)
    assert result["filters"]["adx"] is False


# ─── BTC filter inverted ───────────────────────────────────────────────────

def test_short_signal_passes_btc_when_btc_below_ema():
    """Alt SHORTs need BTC in a downtrend (mirror of LONG's btc>ema)."""
    df = _df_ideal_short()
    cfg = _cfg(use_btc_filter=True)
    result = signals.analyze_short_entry_signal(df, cfg,
                                                btc_close=80000, btc_ema=90000)
    assert result["filters"]["btc_filter"] is True


def test_short_signal_fails_btc_when_btc_above_ema():
    df = _df_ideal_short()
    cfg = _cfg(use_btc_filter=True)
    result = signals.analyze_short_entry_signal(df, cfg,
                                                btc_close=90000, btc_ema=80000)
    assert result["filters"]["btc_filter"] is False


# ─── Full pass + blocked-by reporting ──────────────────────────────────────

def test_short_signal_would_enter_when_all_filters_pass():
    df = _df_ideal_short()
    result = signals.analyze_short_entry_signal(df, _cfg())
    assert result["would_enter"] is True
    assert result["blocked_by"] is None


def test_short_signal_reports_first_failing_filter_only():
    df = _df_ideal_short(ema_fast=[101, 102, 103], ema_slow=[99, 99, 99])
    result = signals.analyze_short_entry_signal(df, _cfg())
    assert result["blocked_by"] == "trend"
    assert result["would_enter"] is False


# ─── Insufficient data ─────────────────────────────────────────────────────

def test_short_signal_handles_insufficient_data():
    df = pd.DataFrame({"close": [100, 100]})
    result = signals.analyze_short_entry_signal(df, _cfg())
    assert result["blocked_by"] == "insufficient_data"
    assert result["would_enter"] is False
