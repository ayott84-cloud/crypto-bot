"""Jul 30 audit item #2 — parameter-plateau sweep for the trailing flips.

Gate A (backtest-expert) requires a stable PLATEAU across parameter
variation, not a narrow optimum. The Jul 16-17 A/B chose arm 1.0 /
trail 1.0 for ETH_4H + SOL_4H from three discrete arms — this sweep
replays a +-20% grid (arm x trail in {0.8, 1.0, 1.2}) on the same
window to confirm the chosen point sits on a plateau.

Run: python -m pytest tests/test_trailing_plateau_sweep.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

from tools.sweep_trailing_plateau import grid_cfgs, plateau_verdict


def test_grid_builds_nine_variant_cfgs():
    base = {"symbol": "ETHUSDT", "use_trailing_exit": True,
             "trail_arm_atr_mult": 1.0, "trail_atr_mult": 1.0}
    grid = grid_cfgs(base)
    assert len(grid) == 9
    assert grid[(0.8, 1.2)]["trail_arm_atr_mult"] == 0.8
    assert grid[(0.8, 1.2)]["trail_atr_mult"] == 1.2
    assert all(c["use_trailing_exit"] is True for c in grid.values())
    # base cfg untouched
    assert base["trail_arm_atr_mult"] == 1.0


def test_plateau_verdict_stable_grid():
    cells = {(a, t): {"pf": 1.5 + 0.05 * a, "n": 40}
              for a in (0.8, 1.0, 1.2) for t in (0.8, 1.0, 1.2)}
    v = plateau_verdict(cells, center=(1.0, 1.0))
    assert v["plateau"] is True
    assert v["min_pf"] >= 1.0
    assert v["center_pf"] == pytest.approx(1.55)


def test_plateau_verdict_flags_narrow_optimum():
    """Center strong, one neighbor collapses below 1.0 — knife's edge."""
    cells = {(a, t): {"pf": 1.6, "n": 40}
              for a in (0.8, 1.0, 1.2) for t in (0.8, 1.0, 1.2)}
    cells[(1.2, 0.8)] = {"pf": 0.85, "n": 40}
    v = plateau_verdict(cells, center=(1.0, 1.0))
    assert v["plateau"] is False
    assert v["worst_cell"] == (1.2, 0.8)
    assert v["min_pf"] == pytest.approx(0.85)


def test_plateau_verdict_ignores_tiny_samples():
    """Cells under the sample floor can't fail the plateau — n=2 noise
    is not evidence of collapse."""
    cells = {(a, t): {"pf": 1.5, "n": 40}
              for a in (0.8, 1.0, 1.2) for t in (0.8, 1.0, 1.2)}
    cells[(0.8, 0.8)] = {"pf": 0.2, "n": 2}
    v = plateau_verdict(cells, center=(1.0, 1.0))
    assert v["plateau"] is True
