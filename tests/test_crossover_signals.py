"""Phase N — crossover_signals tests.

TDD: each test defines one signal/exit invariant. analyze_crossover_entry
fires on the bar where SMA(close, fast) crosses SMA(close, slow). Exit is
a fixed -sl_pct% / +tp_pct% bracket (default 1% / 2% = 1:2 R/R).
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


# ─── Fixtures ──────────────────────────────────────────────────────────

def _crossover_cfg(**overrides) -> dict:
    base = {
        "sma_fast":     50,
        "sma_slow":     100,
        "sl_pct":       1.0,
        "tp_pct":       2.0,
        "allow_short":  True,
    }
    base.update(overrides)
    return base


def _df_from_closes(closes) -> "pd.DataFrame":
    n = len(closes)
    return pd.DataFrame({
        "open":   closes,
        "high":   [c * 1.001 for c in closes],
        "low":    [c * 0.999 for c in closes],
        "close":  closes,
        "volume": [1000] * n,
    })


def _flat_then_step(flat_len: int, flat_value: float,
                       step_count: int, step_value: float):
    """flat_len bars at flat_value, then step_count bars at step_value.
    With flat_len=100 and step_count=2:
      - at iloc[-2] (the cross bar) SMA50 and SMA100 have just inverted
      - at iloc[-3] (prev bar) they were still equal/below
    """
    return [flat_value] * flat_len + [step_value] * step_count


# ─── analyze_crossover_entry ───────────────────────────────────────────

def test_long_entry_on_golden_cross():
    """Prev bar: SMA50 <= SMA100. Current bar: SMA50 > SMA100."""
    from crossover_signals import analyze_crossover_entry
    # 100 bars at 100 → SMA50 = SMA100 = 100 on bar 99
    # bar 100 at 101 → SMA50 (avg of rows 51..100) > SMA100 (avg of rows 1..100)
    # bar 101 is the "in-progress" bar (iloc[-1]); signal evaluates iloc[-2]
    closes = _flat_then_step(100, 100.0, 2, 101.0)
    df = _df_from_closes(closes)
    result = analyze_crossover_entry(df, _crossover_cfg())
    assert result["would_enter"] is True, result
    assert result["direction"] == "LONG"
    assert result["blocked_by"] is None


def test_short_entry_on_death_cross():
    """Prev bar: SMA50 >= SMA100. Current bar: SMA50 < SMA100."""
    from crossover_signals import analyze_crossover_entry
    closes = _flat_then_step(100, 100.0, 2, 99.0)
    df = _df_from_closes(closes)
    result = analyze_crossover_entry(df, _crossover_cfg())
    assert result["would_enter"] is True, result
    assert result["direction"] == "SHORT"
    assert result["blocked_by"] is None


def test_no_entry_when_already_long_trend_no_fresh_cross():
    """SMA50 has been above SMA100 for many bars — no crossover trigger."""
    from crossover_signals import analyze_crossover_entry
    # 100 bars at 100, then 10 bars at 105 — by bar 109, SMA50 has been
    # > SMA100 for 9 bars. Last bar isn't a fresh crossover.
    closes = _flat_then_step(100, 100.0, 10, 105.0)
    df = _df_from_closes(closes)
    result = analyze_crossover_entry(df, _crossover_cfg())
    assert result["would_enter"] is False
    assert result["blocked_by"] == "no_crossover"


def test_no_entry_when_already_short_trend_no_fresh_cross():
    from crossover_signals import analyze_crossover_entry
    closes = _flat_then_step(100, 100.0, 10, 95.0)
    df = _df_from_closes(closes)
    result = analyze_crossover_entry(df, _crossover_cfg())
    assert result["would_enter"] is False
    assert result["blocked_by"] == "no_crossover"


def test_blocked_by_insufficient_data_when_under_slow_period():
    from crossover_signals import analyze_crossover_entry
    closes = [100.0] * 50    # less than slow=100
    df = _df_from_closes(closes)
    result = analyze_crossover_entry(df, _crossover_cfg())
    assert result["would_enter"] is False
    assert result["blocked_by"] == "insufficient_data"


def test_short_cross_blocked_when_allow_short_disabled():
    from crossover_signals import analyze_crossover_entry
    closes = _flat_then_step(100, 100.0, 2, 99.0)
    df = _df_from_closes(closes)
    result = analyze_crossover_entry(df, _crossover_cfg(allow_short=False))
    assert result["would_enter"] is False
    assert result["blocked_by"] == "allow_short_disabled"


def test_signal_values_dict_populated_on_cross():
    """The result should expose sma_fast/sma_slow values for observability."""
    from crossover_signals import analyze_crossover_entry
    closes = _flat_then_step(100, 100.0, 2, 101.0)
    df = _df_from_closes(closes)
    result = analyze_crossover_entry(df, _crossover_cfg())
    assert "sma_fast" in result["values"]
    assert "sma_slow" in result["values"]
    assert "sma_fast_prev" in result["values"]
    assert "sma_slow_prev" in result["values"]
    assert result["values"]["sma_fast"] > result["values"]["sma_slow"]
    assert result["values"]["sma_fast_prev"] <= result["values"]["sma_slow_prev"]


# ─── check_crossover_exit ─────────────────────────────────────────────

def test_long_exit_sl_hit_at_negative_pct():
    from crossover_signals import check_crossover_exit
    cfg = _crossover_cfg()
    # entry 100, SL = 99.0 (–1%)
    assert check_crossover_exit(100.0, 99.0, "LONG", cfg) == "SL Hit"
    assert check_crossover_exit(100.0, 98.9, "LONG", cfg) == "SL Hit"


def test_long_exit_tp_hit_at_positive_pct():
    from crossover_signals import check_crossover_exit
    cfg = _crossover_cfg()
    # entry 100, TP = 102.0 (+2%)
    assert check_crossover_exit(100.0, 102.0, "LONG", cfg) == "TP Hit"
    assert check_crossover_exit(100.0, 102.5, "LONG", cfg) == "TP Hit"


def test_short_exit_sl_hit_when_price_rises():
    from crossover_signals import check_crossover_exit
    cfg = _crossover_cfg()
    assert check_crossover_exit(100.0, 101.0, "SHORT", cfg) == "SL Hit"


def test_short_exit_tp_hit_when_price_drops():
    from crossover_signals import check_crossover_exit
    cfg = _crossover_cfg()
    assert check_crossover_exit(100.0, 98.0, "SHORT", cfg) == "TP Hit"


def test_exit_none_when_in_band():
    from crossover_signals import check_crossover_exit
    cfg = _crossover_cfg()
    assert check_crossover_exit(100.0, 99.5, "LONG", cfg) is None
    assert check_crossover_exit(100.0, 101.5, "LONG", cfg) is None
    assert check_crossover_exit(100.0, 100.5, "SHORT", cfg) is None
    assert check_crossover_exit(100.0, 99.0, "SHORT", cfg) is None
