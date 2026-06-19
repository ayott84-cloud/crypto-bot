"""Phase L.3.2 — volatility-adaptive position sizing tests.

Covers:
  - risk.vol_scaled_margin scaling table
  - position_manager.calculate_position_quantity respects margin_override
  - Integration: momentum + breakout call the helper at entry
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

import risk


# ─── vol_scaled_margin scaling table ────────────────────────────────────

def test_low_vol_returns_full_base():
    assert risk.vol_scaled_margin(50.0, "low") == 50.0


def test_high_vol_reduces_to_70pct():
    assert risk.vol_scaled_margin(50.0, "high") == pytest.approx(35.0)


def test_unknown_vol_passes_through_at_full():
    """Under uncertainty, fail safe (no scaling) not closed (zero margin)."""
    assert risk.vol_scaled_margin(50.0, "unknown") == 50.0
    assert risk.vol_scaled_margin(50.0, None) == 50.0


def test_unrecognized_label_falls_back_to_1x():
    """If regime.py adds a new label, sizing must not silently zero out."""
    assert risk.vol_scaled_margin(50.0, "extreme_left_tail") == 50.0


def test_zero_or_negative_base_returns_zero():
    assert risk.vol_scaled_margin(0, "high") == 0.0
    assert risk.vol_scaled_margin(-10, "low") == 0.0


def test_is_high_vol_predicate():
    assert risk.is_high_vol("high") is True
    assert risk.is_high_vol("low") is False
    assert risk.is_high_vol("unknown") is False
    assert risk.is_high_vol(None) is False


# ─── calculate_position_quantity with margin override ───────────────────

def test_calculate_position_quantity_uses_override():
    from position_manager import calculate_position_quantity
    ex = MagicMock()
    ex.get_qty_step.return_value = 0.001
    ex.get_min_qty.return_value = 0.001
    # base $50 × 10x / $100 = 5.0  vs override $35 × 10x / $100 = 3.5
    qty_default = calculate_position_quantity("XYZ", 100.0, 10, ex)
    qty_scaled = calculate_position_quantity(
        "XYZ", 100.0, 10, ex, margin_override=35.0)
    assert float(qty_default) > float(qty_scaled)
    assert float(qty_scaled) == pytest.approx(3.5, rel=0.01)


def test_calculate_position_quantity_default_unchanged():
    """With no override, behavior is byte-identical to before L.3.2."""
    from position_manager import calculate_position_quantity
    ex = MagicMock()
    ex.get_qty_step.return_value = 0.001
    ex.get_min_qty.return_value = 0.001
    qty = calculate_position_quantity("XYZ", 100.0, 10, ex)
    assert float(qty) == pytest.approx(5.0, rel=0.01)


# ─── Integration smoke: helper available where needed ───────────────────

def test_main_imports_vol_scaled_margin():
    """Sanity: the momentum bot's entry path can import the helper."""
    text = (BOT_DIR / "main.py").read_text(encoding="utf-8")
    assert "vol_scaled_margin" in text


def test_breakout_main_imports_vol_scaled_margin():
    text = (BOT_DIR / "breakout_main.py").read_text(encoding="utf-8")
    assert "vol_scaled_margin" in text


def test_calculate_position_quantity_accepts_override_kwarg():
    """Public API exposes the margin_override kwarg."""
    import inspect
    from position_manager import calculate_position_quantity
    sig = inspect.signature(calculate_position_quantity)
    assert "margin_override" in sig.parameters
