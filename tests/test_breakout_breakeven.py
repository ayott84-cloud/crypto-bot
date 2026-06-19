"""Phase L.3.1 — breakeven-after-favorable-move tests for breakout.

Covers check_breakeven_trigger logic + the ratcheted SL behavior in
check_breakout_exit, plus per-asset config flag wiring.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

from breakout_signals import (
    check_breakeven_trigger, check_breakout_exit,
)


# ─── check_breakeven_trigger ─────────────────────────────────────────────

def test_breakeven_off_when_flag_disabled():
    cfg = {"use_breakeven_after_tp1": False}
    # Even with huge favorable move, flag-off means no trigger
    assert check_breakeven_trigger(
        close=120, entry_price=100, atr_at_entry=5.0,
        direction="LONG", cfg=cfg) is False


def test_breakeven_triggers_at_1_atr_favorable_long():
    cfg = {"use_breakeven_after_tp1": True}
    # exactly +1 ATR
    assert check_breakeven_trigger(
        close=105.0, entry_price=100.0, atr_at_entry=5.0,
        direction="LONG", cfg=cfg) is True


def test_breakeven_does_not_trigger_below_threshold_long():
    cfg = {"use_breakeven_after_tp1": True}
    # +0.5 ATR — half the trigger
    assert check_breakeven_trigger(
        close=102.5, entry_price=100.0, atr_at_entry=5.0,
        direction="LONG", cfg=cfg) is False


def test_breakeven_triggers_at_1_atr_favorable_short():
    cfg = {"use_breakeven_after_tp1": True}
    # short profits when price falls; close=95 with entry=100, atr=5 = +1 ATR
    assert check_breakeven_trigger(
        close=95.0, entry_price=100.0, atr_at_entry=5.0,
        direction="SHORT", cfg=cfg) is True


def test_breakeven_does_not_trigger_adverse_long():
    cfg = {"use_breakeven_after_tp1": True}
    # close BELOW entry — adverse, never triggers
    assert check_breakeven_trigger(
        close=95.0, entry_price=100.0, atr_at_entry=5.0,
        direction="LONG", cfg=cfg) is False


def test_breakeven_respects_custom_trigger_atr():
    cfg = {"use_breakeven_after_tp1": True, "breakeven_trigger_atr": 2.0}
    # +1 ATR no longer enough; need +2 ATR
    assert check_breakeven_trigger(
        close=105.0, entry_price=100.0, atr_at_entry=5.0,
        direction="LONG", cfg=cfg) is False
    assert check_breakeven_trigger(
        close=110.0, entry_price=100.0, atr_at_entry=5.0,
        direction="LONG", cfg=cfg) is True


# ─── check_breakout_exit with ratcheted SL ───────────────────────────────

def _build_exit_df(closes: list[float], n_extra: int = 30) -> "pd.DataFrame":
    """Build a DF long enough for Donchian-exit-period rollings + a trailing
    sequence of closes. Highs/lows mirror closes; volume constant."""
    n = max(len(closes) + n_extra, 30)
    base_closes = [100.0] * (n - len(closes)) + list(closes)
    return pd.DataFrame({
        "close":  base_closes,
        "high":   [c + 0.5 for c in base_closes],
        "low":    [c - 0.5 for c in base_closes],
        "volume": [1000] * n,
    })


def test_exit_breakeven_triggers_be_hit_reason_when_pulled_back():
    """Once breakeven_triggered=True, SL ratchets to entry_price. A pullback
    to entry triggers BE Hit (NOT SL Hit, which would be entry - 2.5×ATR)."""
    df = _build_exit_df(closes=[105, 103, 100.0])  # pulls back to entry
    cfg = {
        "donchian_exit_period": 10, "sl_atr_mult": 2.5,
        "use_breakeven_after_tp1": True, "breakeven_trigger_atr": 1.0,
    }
    reason, kind = check_breakout_exit(
        df, position_direction="LONG",
        entry_price=100.0, atr_at_entry=5.0,
        current_adx=22, cfg=cfg,
        breakeven_triggered=True,
    )
    assert reason == "BE Hit"
    assert kind == "full"


def test_exit_falls_back_to_wide_sl_when_not_triggered():
    """When breakeven_triggered=False, the original 2.5×ATR SL still
    applies — pulling back to entry shouldn't trigger SL."""
    df = _build_exit_df(closes=[105, 103, 100.0])
    cfg = {
        "donchian_exit_period": 10, "sl_atr_mult": 2.5,
        "use_breakeven_after_tp1": True, "breakeven_trigger_atr": 1.0,
    }
    reason, kind = check_breakout_exit(
        df, position_direction="LONG",
        entry_price=100.0, atr_at_entry=5.0,
        current_adx=22, cfg=cfg,
        breakeven_triggered=False,
    )
    # entry - 2.5*5 = 87.5; close=100 is well above. SL should NOT fire.
    # ADX 22 > 15 so no ADX exit. No Donchian cross.
    assert reason is None


def test_exit_short_breakeven_uses_entry_as_sl():
    """SHORT side: breakeven SL at entry_price. A bounce to entry triggers
    BE Hit."""
    df = _build_exit_df(closes=[95, 97, 100.0])
    cfg = {
        "donchian_exit_period": 10, "sl_atr_mult": 2.5,
        "use_breakeven_after_tp1": True, "breakeven_trigger_atr": 1.0,
    }
    reason, kind = check_breakout_exit(
        df, position_direction="SHORT",
        entry_price=100.0, atr_at_entry=5.0,
        current_adx=22, cfg=cfg,
        breakeven_triggered=True,
    )
    assert reason == "BE Hit"


# ─── Per-asset config flag wiring ─────────────────────────────────────────

def test_phase_k_breakout_assets_have_breakeven_on():
    from breakout_config import BREAKOUT_ASSETS
    for key, cfg in BREAKOUT_ASSETS.items():
        if key.endswith("_1H") or key.endswith("_D20"):
            assert cfg.get("use_breakeven_after_tp1") is True, (
                f"Phase K breakout asset {key} should have "
                "use_breakeven_after_tp1=True")
            assert cfg.get("breakeven_trigger_atr") == 1.0


def test_legacy_breakout_assets_do_not_have_breakeven():
    """Legacy 3 4H baselines stay on the original SL-only path until each
    is individually backtested with the L.3.1 ratchet on."""
    from breakout_config import BREAKOUT_ASSETS
    for key in ("BTC_4H", "ETH_4H", "SOL_4H"):
        if key in BREAKOUT_ASSETS:
            assert BREAKOUT_ASSETS[key].get("use_breakeven_after_tp1", False) is False
