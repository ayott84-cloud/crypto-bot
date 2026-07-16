"""tools/ab_breakout_trailing.py — per-asset trailing-exit A/B harness,
plus the backtest_replay CLI additions the momentum re-window needs
(--include-candidates, --assets).

Pure-function tests only; no network.

Run: python -m pytest tests/test_ab_breakout_trailing.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


# ─── variant_cfgs ──────────────────────────────────────────────────────────

def _base_cfg() -> dict:
    return {"symbol": "BTCUSDT", "interval": "4h",
            "use_trailing_exit": True,
            "trail_arm_atr_mult": 1.5, "trail_atr_mult": 1.0}


def test_variant_cfgs_returns_four_arms():
    from tools.ab_breakout_trailing import variant_cfgs
    arms = variant_cfgs(_base_cfg())
    assert set(arms) == {"live", "no_trail", "wide_trail", "early_arm"}


def test_variant_cfgs_arm_semantics():
    from tools.ab_breakout_trailing import variant_cfgs
    arms = variant_cfgs(_base_cfg())
    assert arms["live"]["use_trailing_exit"] is True
    assert arms["live"]["trail_atr_mult"] == 1.0
    assert arms["no_trail"]["use_trailing_exit"] is False
    assert arms["wide_trail"]["use_trailing_exit"] is True
    assert arms["wide_trail"]["trail_atr_mult"] == 2.0
    assert arms["early_arm"]["trail_arm_atr_mult"] == 1.0
    assert arms["early_arm"]["trail_atr_mult"] == 1.0


def test_variant_cfgs_does_not_mutate_input():
    from tools.ab_breakout_trailing import variant_cfgs
    base = _base_cfg()
    variant_cfgs(base)
    assert base == _base_cfg()


# ─── verdict ───────────────────────────────────────────────────────────────

def test_verdict_switch_when_challenger_clearly_wins():
    from tools.ab_breakout_trailing import verdict
    rows = {"live":       {"pf": 1.20, "n": 30, "dd": 10.0},
            "no_trail":   {"pf": 1.45, "n": 28, "dd": 9.0},
            "wide_trail": {"pf": 1.25, "n": 30, "dd": 12.0},
            "early_arm":  {"pf": 1.10, "n": 31, "dd": 8.0}}
    assert verdict(rows) == "SWITCH -> no_trail"


def test_verdict_keeps_live_within_epsilon():
    """A challenger must beat the incumbent by PF >= +0.10 (the Phase M/N
    sweep pass criterion) — a 0.03 edge is noise."""
    from tools.ab_breakout_trailing import verdict
    rows = {"live":     {"pf": 1.40, "n": 30, "dd": 10.0},
            "no_trail": {"pf": 1.43, "n": 30, "dd": 10.0}}
    assert verdict(rows) == "KEEP live"


def test_verdict_keeps_live_when_live_is_best():
    from tools.ab_breakout_trailing import verdict
    rows = {"live":     {"pf": 1.50, "n": 30, "dd": 10.0},
            "no_trail": {"pf": 1.20, "n": 30, "dd": 9.0}}
    assert verdict(rows) == "KEEP live"


def test_verdict_insufficient_sample():
    from tools.ab_breakout_trailing import verdict
    rows = {"live":     {"pf": 2.00, "n": 3, "dd": 5.0},
            "no_trail": {"pf": 9.00, "n": 2, "dd": 1.0}}
    assert verdict(rows) == "INSUFFICIENT n"


def test_verdict_ignores_small_sample_challengers():
    """A challenger with n=3 can print PF 9.0 by luck — only arms with
    n >= 5 compete."""
    from tools.ab_breakout_trailing import verdict
    rows = {"live":       {"pf": 1.40, "n": 30, "dd": 10.0},
            "no_trail":   {"pf": 9.00, "n": 3,  "dd": 1.0},
            "wide_trail": {"pf": 1.35, "n": 28, "dd": 11.0}}
    assert verdict(rows) == "KEEP live"


# ─── backtest_replay CLI additions (momentum re-window) ───────────────────

def test_momentum_universe_excludes_candidates_by_default():
    from tools.backtest_replay import _momentum_universe
    base = _momentum_universe(False)
    assert "ETH_1D" not in base          # demoted Jul 4
    assert "SOL" not in base
    assert "BTC" in base                  # live set intact


def test_momentum_universe_includes_candidates_on_flag():
    from tools.backtest_replay import _momentum_universe
    full = _momentum_universe(True)
    for key in ("ETH_1D", "SOL", "SHIB_1D"):
        assert key in full, f"candidate {key} missing"
    base = _momentum_universe(False)
    assert set(base) <= set(full)


def test_filter_universe():
    from tools.backtest_replay import _filter_universe
    uni = {"A": 1, "B": 2, "C": 3}
    assert _filter_universe(uni, None) == uni
    assert _filter_universe(uni, "") == uni
    assert _filter_universe(uni, "A, C") == {"A": 1, "C": 3}
    assert _filter_universe(uni, "nope") == {}
