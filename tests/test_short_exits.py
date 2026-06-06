"""Phase E.2 — SHORT exit conditions + rotation SL/TP reconstruction.

Two pieces:
  1. check_short_exit_conditions() — mirror of check_exit_conditions
     with SL above entry, TP1/TP2 below entry, stale check inverted.
  2. _reconstruct_levels(direction, entry, atr, cfg) helper for the
     rotation-close notification path at main.py — currently hardcoded
     LONG math, will flip signs for SHORT positions.

Run: python -m pytest tests/test_short_exits.py -v
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
    base = {
        "sl_atr_mult": 1.0, "tp1_atr_mult": 1.0, "tp2_atr_mult": 2.0,
        "stale_bars": 12, "stale_threshold_mult": 0.5,
        "use_breakeven_after_tp1": False,
    }
    base.update(overrides)
    return base


# ─── check_short_exit_conditions: SL ───────────────────────────────────────

def test_short_sl_triggers_when_price_rises_above_entry_plus_atr():
    """SL for SHORT is ENTRY + sl_atr_mult * ATR. Price above = stop hit."""
    reason, kind = signals.check_short_exit_conditions(
        entry_price=100.0, atr_at_entry=2.0, current_price=103.0,
        bars_since_entry=1, phase="full", cfg=_cfg())
    assert reason == "SL Hit"
    assert kind == "full"


def test_short_sl_does_not_trigger_below_entry():
    reason, kind = signals.check_short_exit_conditions(
        entry_price=100.0, atr_at_entry=2.0, current_price=99.0,
        bars_since_entry=1, phase="full", cfg=_cfg())
    assert reason != "SL Hit"


# ─── check_short_exit_conditions: TP1 / TP2 ────────────────────────────────

def test_short_tp1_triggers_when_price_drops_below_entry_minus_atr():
    """TP1 for SHORT is ENTRY - tp1_atr_mult * ATR. Price at/below = partial."""
    reason, kind = signals.check_short_exit_conditions(
        entry_price=100.0, atr_at_entry=2.0, current_price=98.0,
        bars_since_entry=1, phase="full", cfg=_cfg())
    assert reason == "TP1 Hit"
    assert kind == "partial"


def test_short_tp2_only_triggers_after_tp1_taken():
    """TP2 logic: only fires when phase='tp1_taken'."""
    # In 'full' phase, price below TP2 returns TP1
    reason, kind = signals.check_short_exit_conditions(
        entry_price=100.0, atr_at_entry=2.0, current_price=96.0,
        bars_since_entry=1, phase="full", cfg=_cfg())
    assert reason == "TP1 Hit"
    # In 'tp1_taken' phase, same price returns TP2
    reason, kind = signals.check_short_exit_conditions(
        entry_price=100.0, atr_at_entry=2.0, current_price=96.0,
        bars_since_entry=1, phase="tp1_taken", cfg=_cfg())
    assert reason == "TP2 Hit"
    assert kind == "full"


# ─── Breakeven-after-TP1 ───────────────────────────────────────────────────

def test_short_breakeven_stop_after_tp1_when_enabled():
    """In tp1_taken phase, with use_breakeven_after_tp1, SL = entry_price."""
    cfg = _cfg(use_breakeven_after_tp1=True)
    # Price has come back to entry → BE stop should hit
    reason, kind = signals.check_short_exit_conditions(
        entry_price=100.0, atr_at_entry=2.0, current_price=100.0,
        bars_since_entry=5, phase="tp1_taken", cfg=cfg)
    assert reason == "BE Hit"
    assert kind == "full"


def test_short_breakeven_does_not_trigger_when_still_profitable():
    """In tp1_taken phase with BE enabled, price still below entry = OK."""
    cfg = _cfg(use_breakeven_after_tp1=True)
    reason, kind = signals.check_short_exit_conditions(
        entry_price=100.0, atr_at_entry=2.0, current_price=98.5,
        bars_since_entry=5, phase="tp1_taken", cfg=cfg)
    assert reason != "BE Hit"


# ─── Stale exit (inverted) ─────────────────────────────────────────────────

def test_short_stale_exit_when_price_has_not_dropped_enough():
    """Stale: > stale_bars elapsed AND current price > stale_level
    (price should have moved DOWN for SHORT but hasn't)."""
    cfg = _cfg(stale_bars=10, stale_threshold_mult=0.5, tp1_atr_mult=1.0)
    # stale_level = 100 - 2 * 1.0 * 0.5 = 99
    # price still at 99.5 (above stale_level) after 15 bars
    reason, kind = signals.check_short_exit_conditions(
        entry_price=100.0, atr_at_entry=2.0, current_price=99.5,
        bars_since_entry=15, phase="full", cfg=cfg)
    assert reason == "Stale Exit"
    assert kind == "full"


def test_short_stale_exit_does_not_trigger_when_price_did_move_down():
    """If price moved well below stale_level, no stale exit even after many bars."""
    cfg = _cfg(stale_bars=10, stale_threshold_mult=0.5, tp1_atr_mult=1.0)
    # stale_level = 99; price at 97 = past it
    reason, kind = signals.check_short_exit_conditions(
        entry_price=100.0, atr_at_entry=2.0, current_price=97.0,
        bars_since_entry=15, phase="full", cfg=cfg)
    # Either no exit, or TP1 (since 97 < 99 = entry - tp1_mult*atr)
    assert reason != "Stale Exit"


# ─── No exit case ──────────────────────────────────────────────────────────

def test_short_no_exit_when_price_in_range():
    """Price between SL and TP1 with no stale → no exit."""
    cfg = _cfg(stale_bars=10)
    reason, kind = signals.check_short_exit_conditions(
        entry_price=100.0, atr_at_entry=2.0, current_price=99.0,
        bars_since_entry=1, phase="full", cfg=cfg)
    assert reason is None
    assert kind is None


# ─── Rotation SL/TP reconstruction helper ─────────────────────────────────

def test_reconstruct_levels_long_uses_long_math():
    """LONG: TP above entry, SL below."""
    cfg = {"tp1_atr_mult": 1.0, "tp2_atr_mult": 2.0, "sl_atr_mult": 1.5}
    levels = signals.reconstruct_position_levels(
        direction="LONG", entry_price=100.0, atr_at_entry=2.0, cfg=cfg)
    assert levels["tp1_price"] == pytest.approx(102.0)
    assert levels["tp2_price"] == pytest.approx(104.0)
    assert levels["sl_price"]  == pytest.approx(97.0)


def test_reconstruct_levels_short_uses_short_math():
    """SHORT: TP below entry, SL above."""
    cfg = {"tp1_atr_mult": 1.0, "tp2_atr_mult": 2.0, "sl_atr_mult": 1.5}
    levels = signals.reconstruct_position_levels(
        direction="SHORT", entry_price=100.0, atr_at_entry=2.0, cfg=cfg)
    assert levels["tp1_price"] == pytest.approx(98.0)
    assert levels["tp2_price"] == pytest.approx(96.0)
    assert levels["sl_price"]  == pytest.approx(103.0)


def test_reconstruct_levels_defaults_to_long_for_unknown_direction():
    """Defensive: if direction is somehow missing, default to LONG math
    so we don't silently flip signs on a real position."""
    cfg = {"tp1_atr_mult": 1.0, "tp2_atr_mult": 2.0, "sl_atr_mult": 1.5}
    levels = signals.reconstruct_position_levels(
        direction="", entry_price=100.0, atr_at_entry=2.0, cfg=cfg)
    assert levels["tp1_price"] == pytest.approx(102.0)
    assert levels["sl_price"] == pytest.approx(97.0)
