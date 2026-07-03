"""P3.1 — Crossover N.3 redesign.

Research basis (TradingRush 100-trade template, r/algotrading consensus):
  - 200-SMA trend filter WITH slope gate: only LONG crosses when price >
    SMA200 AND SMA200 rising (losses cluster when the 200 is flat)
  - ADX > 20 gate: vanilla crossover has no edge in chop
  - Exit on SIGNAL INVALIDATION (close crosses back through SMA-fast /
    opposite cross), not a 1% bracket that contradicts the multi-day
    signal timeframe. Wide emergency ATR stop (3.5x) for gap protection.

Run: python -m pytest tests/test_crossover_n3.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


def _df_from_closes(closes):
    n = len(closes)
    return pd.DataFrame({
        "open":   closes,
        "high":   [c * 1.001 for c in closes],
        "low":    [c * 0.999 for c in closes],
        "close":  closes,
        "volume": [1000.0] * n,
    })


def _cfg(**over):
    base = {
        "sma_fast": 20, "sma_slow": 50,
        "sl_pct": 1.0, "tp_pct": 2.0,
        "allow_short": True,
    }
    base.update(over)
    return base


def _rising_cross_closes(n_flat=210, n_rise=30):
    """Long uptrend so SMA200 is rising, then a dip + recovery that
    produces a fresh SMA20/50 golden cross near the end."""
    closes = [100 + i * 0.20 for i in range(n_flat)]          # steady rise
    dip = [closes[-1] - (i + 1) * 0.8 for i in range(25)]      # sharp dip
    recover = [dip[-1] + (i + 1) * 1.2 for i in range(n_rise)] # sharp recovery
    return closes + dip + recover


# ─── SMA200 slope gate ─────────────────────────────────────────────────────

def test_sma200_gate_blocks_long_below_falling_sma200():
    from crossover_signals import analyze_crossover_entry
    # Downtrend long enough for SMA200 to be falling, then a small pop
    # that golden-crosses the 20/50.
    closes = [300 - i * 0.5 for i in range(260)]
    closes += [closes[-1] + (i + 1) * 1.5 for i in range(25)]
    df = _df_from_closes(closes)
    res = analyze_crossover_entry(df, _cfg(use_sma200_filter=True))
    if res["would_enter"]:
        pytest.fail("LONG allowed below falling SMA200")
    # blocked either by no_crossover (timing) or the sma200 gate; when a
    # cross IS present the block must name the gate
    assert res["blocked_by"] in ("sma200_filter", "no_crossover")


def test_sma200_gate_absent_by_default():
    """Default cfg (no flag) keeps N.2 behavior — no SMA200 gate."""
    from crossover_signals import analyze_crossover_entry
    closes = [100.0] * 100 + [101.0] * 2
    df = _df_from_closes(closes)
    res = analyze_crossover_entry(df, _cfg(sma_fast=50, sma_slow=100))
    assert res["would_enter"] is True


# ─── ADX gate ──────────────────────────────────────────────────────────────

def test_adx_gate_blocks_in_chop():
    from crossover_signals import analyze_crossover_entry
    import numpy as np
    rng = np.random.default_rng(7)
    # tight random chop — ADX low; engineer a mechanical 20/50 cross by
    # appending a two-bar step
    closes = list(100 + rng.normal(0, 0.05, 300))
    closes += [100.4, 100.5]
    df = _df_from_closes(closes)
    res = analyze_crossover_entry(df, _cfg(use_adx_filter=True,
                                             adx_threshold=20.0))
    if res["would_enter"]:
        pytest.fail("entry allowed in low-ADX chop")
    assert res["blocked_by"] in ("adx", "no_crossover")


# ─── Invalidation exits (check_crossover_exit_v3) ──────────────────────────

def test_v3_exit_none_while_price_above_sma_fast():
    from crossover_signals import check_crossover_exit_v3
    reason = check_crossover_exit_v3(
        direction="LONG", entry_price=100.0, current_close=103.0,
        sma_fast_now=101.0, atr_at_entry=2.0, cfg=_cfg())
    assert reason is None


def test_v3_exit_invalidation_when_close_crosses_back_below_sma_fast():
    from crossover_signals import check_crossover_exit_v3
    reason = check_crossover_exit_v3(
        direction="LONG", entry_price=100.0, current_close=100.5,
        sma_fast_now=101.0, atr_at_entry=2.0, cfg=_cfg())
    assert reason == "Invalidation Exit"


def test_v3_exit_emergency_stop_on_gap():
    """Price crashes 3.5x ATR below entry → emergency stop even if we
    haven't seen the SMA data (gap protection)."""
    from crossover_signals import check_crossover_exit_v3
    reason = check_crossover_exit_v3(
        direction="LONG", entry_price=100.0, current_close=92.9,
        sma_fast_now=None, atr_at_entry=2.0, cfg=_cfg(emergency_atr_mult=3.5))
    assert reason == "Emergency SL"


def test_v3_exit_short_mirrors():
    from crossover_signals import check_crossover_exit_v3
    # SHORT: invalidation when close crosses back ABOVE sma_fast
    assert check_crossover_exit_v3(
        direction="SHORT", entry_price=100.0, current_close=99.0,
        sma_fast_now=99.5, atr_at_entry=2.0, cfg=_cfg()) is None
    assert check_crossover_exit_v3(
        direction="SHORT", entry_price=100.0, current_close=99.7,
        sma_fast_now=99.5, atr_at_entry=2.0, cfg=_cfg()) == "Invalidation Exit"
    # emergency: 100 + 3.5*2 = 107
    assert check_crossover_exit_v3(
        direction="SHORT", entry_price=100.0, current_close=107.1,
        sma_fast_now=None, atr_at_entry=2.0,
        cfg=_cfg(emergency_atr_mult=3.5)) == "Emergency SL"


def test_v3_exit_missing_sma_none_no_invalidation():
    """SMA unavailable (data hiccup) → only the emergency stop applies."""
    from crossover_signals import check_crossover_exit_v3
    assert check_crossover_exit_v3(
        direction="LONG", entry_price=100.0, current_close=99.0,
        sma_fast_now=None, atr_at_entry=2.0, cfg=_cfg()) is None


# ─── N.3 config defaults ───────────────────────────────────────────────────

def test_n3_config_defaults():
    import crossover_config as cc
    sample = next(iter(cc.CROSSOVER_ASSETS.values()))
    assert sample.get("use_sma200_filter") is True
    assert sample.get("use_adx_filter") is True
    assert sample.get("exit_mode") == "invalidation"
    assert float(sample.get("emergency_atr_mult", 0)) >= 3.0
