"""Phase L.3.3 — breakout pyramiding tests.

Critical invariant (peer-review): pyramid legs live INSIDE the existing
position dict as a `pyramid_legs: list[dict]` field. They must NOT
appear as new top-level state keys — `count_open_positions` would
double/triple-count them against MAX_POSITIONS otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

from breakout_signals import analyze_breakout_pyramid


# ─── Pyramid trigger conditions ─────────────────────────────────────────

def _build_df_with_breakout(n: int = 80, high_water: float = 110.0):
    """Build a DF where last close clears the prior Donchian high."""
    rising = [100.0 + i * 0.1 for i in range(n - 1)] + [high_water]
    df = pd.DataFrame({
        "close": rising,
        "high":  [c + 0.3 for c in rising],
        "low":   [c - 0.3 for c in rising],
        "atr":   [1.0] * n,
        "volume": [1000] * n,
    })
    return df


def test_pyramid_blocked_when_flag_off():
    df = _build_df_with_breakout()
    pos = {"entry_price": 100.0, "atr_at_entry": 5.0, "direction": "LONG"}
    cfg = {"donchian_period": 55, "allow_pyramiding": False}
    assert analyze_breakout_pyramid(df, pos, cfg) is None


def test_pyramid_blocked_when_max_legs_reached():
    df = _build_df_with_breakout()
    pos = {
        "entry_price": 100.0, "atr_at_entry": 5.0, "direction": "LONG",
        "pyramid_legs": [
            {"entry_price": 106, "atr_at_entry": 5.0, "quantity": 0.5},
            {"entry_price": 108, "atr_at_entry": 5.0, "quantity": 0.5},
        ],
    }
    cfg = {"donchian_period": 55, "allow_pyramiding": True,
            "max_pyramid_legs": 2, "pyramid_trigger_atr": 1.0}
    assert analyze_breakout_pyramid(df, pos, cfg) is None


def test_pyramid_blocked_below_trigger_distance():
    """Close hasn't moved enough above baseline entry."""
    df = _build_df_with_breakout(high_water=102.0)  # only +2 above base entry
    pos = {"entry_price": 100.0, "atr_at_entry": 5.0, "direction": "LONG"}
    cfg = {"donchian_period": 55, "allow_pyramiding": True,
            "max_pyramid_legs": 2, "pyramid_trigger_atr": 1.0}
    # trigger requires +5 favorable; only +2
    assert analyze_breakout_pyramid(df, pos, cfg) is None


def test_pyramid_fires_when_all_conditions_met_long():
    df = _build_df_with_breakout(high_water=120.0)  # well above prior channel + entry
    pos = {"entry_price": 100.0, "atr_at_entry": 5.0, "direction": "LONG"}
    cfg = {"donchian_period": 55, "allow_pyramiding": True,
            "max_pyramid_legs": 2, "pyramid_trigger_atr": 1.0,
            "pyramid_size_fraction": 0.5}
    spec = analyze_breakout_pyramid(df, pos, cfg)
    assert spec is not None
    assert spec["direction"] == "LONG"
    assert spec["entry_price"] == 120.0
    assert spec["size_fraction"] == 0.5


def test_pyramid_uses_last_leg_as_anchor():
    """When a pyramid leg already exists, trigger distance is measured
    from that leg's entry — not the baseline."""
    df = _build_df_with_breakout(high_water=112.0)  # +12 above baseline, +2 above last leg
    pos = {
        "entry_price": 100.0, "atr_at_entry": 5.0, "direction": "LONG",
        "pyramid_legs": [{"entry_price": 110.0, "atr_at_entry": 5.0, "quantity": 0.5}],
    }
    cfg = {"donchian_period": 55, "allow_pyramiding": True,
            "max_pyramid_legs": 2, "pyramid_trigger_atr": 1.0}
    # Move from last anchor (110) is +2 — below +5 trigger
    assert analyze_breakout_pyramid(df, pos, cfg) is None


def test_pyramid_blocked_when_no_continuation_break():
    """Close hasn't cleared the prior Donchian upper — no continuation."""
    n = 80
    # Build a DF where close is below the prior high (no new Donchian break)
    closes = [100.0 + i * 0.1 for i in range(n - 1)] + [104.0]  # +4 above entry, but below trend high
    df = pd.DataFrame({
        "close": closes,
        # High water mark at 115 in the lookback window — prior Donchian top
        "high":  [115.0 if i < n - 5 else c + 0.3 for i, c in enumerate(closes)],
        "low":   [c - 0.3 for c in closes],
        "atr":   [1.0] * n,
        "volume": [1000] * n,
    })
    pos = {"entry_price": 100.0, "atr_at_entry": 5.0, "direction": "LONG"}
    cfg = {"donchian_period": 55, "allow_pyramiding": True,
            "max_pyramid_legs": 2, "pyramid_trigger_atr": 1.0}
    # close=104 is below prior_upper=115; no continuation → block
    assert analyze_breakout_pyramid(df, pos, cfg) is None


# ─── position_manager aggregate helpers ──────────────────────────────

def test_aggregate_qty_with_no_pyramid():
    from position_manager import aggregate_position_qty
    pos = {"entry_price": 100.0, "quantity": 1.5}
    assert aggregate_position_qty(pos) == pytest.approx(1.5)


def test_aggregate_qty_sums_baseline_and_legs():
    from position_manager import aggregate_position_qty
    pos = {
        "quantity": 1.0,
        "pyramid_legs": [
            {"quantity": 0.5}, {"quantity": 0.5},
        ],
    }
    assert aggregate_position_qty(pos) == pytest.approx(2.0)


def test_aggregate_avg_entry_weighted():
    """Weighted average — baseline 1.0 @ $100 + leg 0.5 @ $110 = avg $103.33."""
    from position_manager import aggregate_avg_entry
    pos = {
        "entry_price": 100.0, "quantity": 1.0,
        "pyramid_legs": [{"entry_price": 110.0, "quantity": 0.5}],
    }
    expected = (100.0 * 1.0 + 110.0 * 0.5) / 1.5
    assert aggregate_avg_entry(pos) == pytest.approx(expected)


def test_aggregate_avg_entry_no_pyramid_returns_baseline():
    from position_manager import aggregate_avg_entry
    pos = {"entry_price": 100.0, "quantity": 1.5}
    assert aggregate_avg_entry(pos) == pytest.approx(100.0)


# ─── Slot-accounting invariant (peer-review's critical fix) ──────────

def test_pyramid_legs_do_not_inflate_count_open_positions():
    """A pyramided position must count as ONE slot, regardless of leg
    count. If pyramid legs were stored as top-level keys (BREAKOUT_X_PYR1),
    count_open_positions would double-count them."""
    from position_manager import count_open_positions
    state = {
        "positions": {
            "BREAKOUT_BTC_1H": {
                "symbol": "BTCUSDT", "direction": "LONG",
                "entry_price": 80000, "quantity": 0.001,
                "pyramid_legs": [
                    {"entry_price": 80500, "quantity": 0.0005},
                    {"entry_price": 80800, "quantity": 0.0005},
                ],
            },
        }
    }
    # One top-level key → one slot regardless of 2 pyramid legs underneath
    assert count_open_positions(state) == 1


# ─── Per-asset config wiring ────────────────────────────────────────

def test_phase_k_breakout_assets_have_pyramiding_on():
    from breakout_config import BREAKOUT_ASSETS
    for key, cfg in BREAKOUT_ASSETS.items():
        if key.endswith("_1H") or key.endswith("_D20"):
            assert cfg.get("allow_pyramiding") is True, (
                f"Phase K breakout asset {key} should have allow_pyramiding=True")
            assert cfg.get("max_pyramid_legs") == 2


def test_legacy_breakout_assets_do_not_have_pyramiding():
    """Legacy 3 4H baselines stay single-entry until each is individually
    backtested with pyramiding on."""
    from breakout_config import BREAKOUT_ASSETS
    for key in ("BTC_4H", "ETH_4H", "SOL_4H"):
        if key in BREAKOUT_ASSETS:
            assert BREAKOUT_ASSETS[key].get("allow_pyramiding", False) is False
