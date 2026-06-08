"""Phase K — Momentum candidate-asset staging tests.

Same invariants as test_breakout_candidates: candidates never auto-trade,
share the live config shape, and the bot's main module doesn't iterate
the candidate dict.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest


def test_candidates_module_importable():
    from config import MOMENTUM_CANDIDATE_ASSETS
    assert isinstance(MOMENTUM_CANDIDATE_ASSETS, dict)
    assert len(MOMENTUM_CANDIDATE_ASSETS) >= 10


def test_candidates_have_required_shape():
    from config import MOMENTUM_CANDIDATE_ASSETS
    required = {"symbol", "interval", "ema_fast", "ema_slow",
                  "atr_period", "rsi_period", "macd_fast",
                  "tp1_atr_mult", "sl_atr_mult", "strategy_name"}
    for name, cfg in MOMENTUM_CANDIDATE_ASSETS.items():
        missing = required - set(cfg.keys())
        assert not missing, f"momentum candidate {name} missing {missing}"


def test_candidates_do_not_overlap_with_live_assets():
    """Promotion = move from CANDIDATE to ASSETS. They must not coexist."""
    from config import MOMENTUM_CANDIDATE_ASSETS, ASSETS
    overlap = set(MOMENTUM_CANDIDATE_ASSETS) & set(ASSETS)
    assert not overlap, f"keys in both dicts: {overlap}"


def test_candidates_not_iterated_by_main_module():
    """main.py must never reference MOMENTUM_CANDIDATE_ASSETS — it would
    silently activate untested strategies."""
    main_path = BOT_DIR / "main.py"
    if not main_path.exists():
        pytest.skip("main.py not present")
    text = main_path.read_text(encoding="utf-8")
    assert "MOMENTUM_CANDIDATE_ASSETS" not in text


def test_candidate_keys_unique():
    """Candidate keys must not duplicate. (symbol, interval) tuples CAN
    duplicate when a variant exists — e.g. LINK_1D vs LINK_1D_TS test
    the same coin/TF with different stop-loss params."""
    from config import MOMENTUM_CANDIDATE_ASSETS
    keys = list(MOMENTUM_CANDIDATE_ASSETS.keys())
    assert len(keys) == len(set(keys)), "duplicate candidate keys"
