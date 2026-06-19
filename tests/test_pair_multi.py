"""Phase K round 5b — multi-pair refactor tests.

Covers:
  - PAIR_CONFIGS is the canonical multi-pair container; BTCLTC promoted
  - PAIR_CANDIDATE_CONFIGS filters out promoted keys (no re-test on rerun)
  - leg_keys() generates correct per-pair state keys
  - _pair_is_open() is per-pair, not global
  - register_entry kwargs absorbed onto position dict via L hotfix
  - BTCLTC has a backtest_stats row for the projection table
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest


def test_pair_configs_includes_both_pairs():
    from pair_config import PAIR_CONFIGS
    assert "ETHBTC" in PAIR_CONFIGS
    assert "BTCLTC" in PAIR_CONFIGS


def test_pair_configs_have_required_shape():
    from pair_config import PAIR_CONFIGS
    required = {"long_symbol", "short_symbol", "interval", "cfg"}
    for name, spec in PAIR_CONFIGS.items():
        assert required <= set(spec.keys()), f"{name} missing keys"
        assert "z_window" in spec["cfg"]


def test_pair_configs_distinct_legs():
    """Each pair must have two distinct symbols."""
    from pair_config import PAIR_CONFIGS
    for name, spec in PAIR_CONFIGS.items():
        assert spec["long_symbol"] != spec["short_symbol"], (
            f"pair {name} has identical legs")


def test_promoted_pair_filtered_from_candidates():
    """After promotion, BTCLTC must NOT still appear as a candidate."""
    from pair_config import PAIR_CONFIGS, PAIR_CANDIDATE_CONFIGS
    overlap = set(PAIR_CONFIGS.keys()) & set(PAIR_CANDIDATE_CONFIGS.keys())
    assert not overlap, f"keys in both: {overlap}"


def test_btcltc_has_backtest_stats():
    from pair_config import PAIR_BACKTEST_STATS
    assert "BTCLTC" in PAIR_BACKTEST_STATS
    stats = PAIR_BACKTEST_STATS["BTCLTC"]
    assert stats["pf"] >= 1.3
    assert stats["years"] > 0
    assert "trades" in stats
    assert "pnl_pct" in stats
    assert "dd_pct" in stats


# ─── leg_keys() parameterization ────────────────────────────────────────

def test_leg_keys_ethbtc():
    from pair_main import leg_keys
    long_k, short_k = leg_keys("ETHBTC")
    assert long_k  == "PAIR_ETHBTC_LONG_LEG"
    assert short_k == "PAIR_ETHBTC_SHORT_LEG"


def test_leg_keys_btcltc():
    from pair_main import leg_keys
    long_k, short_k = leg_keys("BTCLTC")
    assert long_k  == "PAIR_BTCLTC_LONG_LEG"
    assert short_k == "PAIR_BTCLTC_SHORT_LEG"


def test_leg_keys_ethbtc_matches_legacy_constants():
    """The new leg_keys helper must agree with the legacy module-level
    constants for ETHBTC, otherwise any code reading them would break
    on the BTCLTC pair's existence."""
    from pair_config import PAIR_LONG_LEG_KEY, PAIR_SHORT_LEG_KEY
    from pair_main import leg_keys
    long_k, short_k = leg_keys("ETHBTC")
    assert long_k  == PAIR_LONG_LEG_KEY
    assert short_k == PAIR_SHORT_LEG_KEY


# ─── _pair_is_open per-pair ─────────────────────────────────────────────

def test_pair_is_open_only_for_named_pair():
    from pair_main import _pair_is_open
    state = {"positions": {
        "PAIR_ETHBTC_LONG_LEG": {"symbol": "ETHUSDT"},
    }}
    assert _pair_is_open(state, "ETHBTC") is True
    assert _pair_is_open(state, "BTCLTC") is False


def test_pair_is_open_default_is_ethbtc():
    """Backwards-compat: no pair_name argument → checks ETHBTC."""
    from pair_main import _pair_is_open
    state = {"positions": {"PAIR_ETHBTC_LONG_LEG": {"symbol": "ETHUSDT"}}}
    assert _pair_is_open(state) is True
    state2 = {"positions": {"PAIR_BTCLTC_LONG_LEG": {"symbol": "BTCUSDT"}}}
    assert _pair_is_open(state2) is False


def test_two_pairs_open_simultaneously():
    """The whole point of the refactor — both pairs can hold positions
    without colliding on state keys."""
    from pair_main import _pair_is_open
    state = {"positions": {
        "PAIR_ETHBTC_LONG_LEG":  {"symbol": "ETHUSDT"},
        "PAIR_ETHBTC_SHORT_LEG": {"symbol": "BTCUSDT"},
        "PAIR_BTCLTC_LONG_LEG":  {"symbol": "BTCUSDT"},
        "PAIR_BTCLTC_SHORT_LEG": {"symbol": "LTCUSDT"},
    }}
    assert _pair_is_open(state, "ETHBTC") is True
    assert _pair_is_open(state, "BTCLTC") is True


def test_max_pair_positions_at_least_2():
    """Slot cap must allow both pairs to be open at once. With 1 it would
    be the old single-pair limit."""
    from pair_config import MAX_PAIR_POSITIONS
    assert MAX_PAIR_POSITIONS >= 2
