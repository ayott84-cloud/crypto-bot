"""Phase K — Pair candidate-asset staging tests.

Same invariants as test_breakout_candidates / test_momentum_candidates:
candidates don't auto-trade, have valid shape, pair_main doesn't iterate.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest


def test_candidates_module_importable():
    from pair_config import PAIR_CANDIDATE_CONFIGS
    assert isinstance(PAIR_CANDIDATE_CONFIGS, dict)
    assert len(PAIR_CANDIDATE_CONFIGS) >= 1


def test_candidates_have_required_shape():
    from pair_config import PAIR_CANDIDATE_CONFIGS
    required = {"long_symbol", "short_symbol", "interval", "cfg"}
    for name, spec in PAIR_CANDIDATE_CONFIGS.items():
        missing = required - set(spec.keys())
        assert not missing, f"pair candidate {name} missing {missing}"
        cfg = spec["cfg"]
        assert "z_window" in cfg and "entry_z" in cfg, (
            f"pair candidate {name} cfg missing z params")


def test_candidates_long_short_differ():
    """Pair must trade two distinct symbols — same long+short is degenerate."""
    from pair_config import PAIR_CANDIDATE_CONFIGS
    for name, spec in PAIR_CANDIDATE_CONFIGS.items():
        assert spec["long_symbol"] != spec["short_symbol"], (
            f"pair candidate {name} has identical legs")


def test_candidates_not_iterated_by_pair_main():
    """pair_main must never reference PAIR_CANDIDATE_CONFIGS — promoting
    a pair requires explicit refactoring of the multi-pair iteration."""
    main_path = BOT_DIR / "pair_main.py"
    if not main_path.exists():
        pytest.skip("pair_main.py not present")
    text = main_path.read_text(encoding="utf-8")
    assert "PAIR_CANDIDATE_CONFIGS" not in text


def test_replay_pair_accepts_candidate_args():
    """replay_pair must support symbol/cfg overrides for candidate validation."""
    from tools.backtest_replay import replay_pair
    import inspect
    sig = inspect.signature(replay_pair)
    for param in ("asset_name", "long_symbol", "short_symbol",
                    "interval", "cfg"):
        assert param in sig.parameters, f"replay_pair missing {param} param"
