"""Unit tests for position_manager three-way namespace (momentum / whale / funding).

Phase 0.1 of the comprehensive enhancement plan. These tests must fail BEFORE
the namespace extension lands, then pass after.

Run: python -m pytest tests/test_position_namespace.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

from position_manager import (
    _bot_of_key,
    _merge_state,
    find_most_profitable_position,
)


# ─── _bot_of_key classifier ─────────────────────────────────────────────────

def test_bot_of_key_whale_prefix():
    assert _bot_of_key("WHALE_BTC") == "whale"


def test_bot_of_key_funding_prefix():
    assert _bot_of_key("FUNDING_ETH") == "funding"


def test_bot_of_key_breakout_prefix():
    """Phase G: BREAKOUT_ prefix routes to the breakout bot."""
    assert _bot_of_key("BREAKOUT_BTC_4H") == "breakout"
    assert _bot_of_key("BREAKOUT_ETH_1D") == "breakout"


def test_bot_of_key_pair_prefix():
    """Phase F: PAIR_ prefix routes to the pair-trade bot."""
    assert _bot_of_key("PAIR_ETHBTC_LONG_LEG") == "pair"
    assert _bot_of_key("PAIR_ETHBTC_SHORT_LEG") == "pair"


def test_bot_of_key_momentum_default_plain():
    assert _bot_of_key("BTC") == "momentum"


def test_bot_of_key_momentum_default_with_underscore():
    assert _bot_of_key("BTC_4H") == "momentum"


def test_bot_of_key_momentum_default_lowercase_strategy():
    # Future-proof: anything not prefixed by WHALE_/FUNDING_ is momentum.
    assert _bot_of_key("XRP_4H_v2") == "momentum"


# ─── _merge_state preserves three namespaces ────────────────────────────────

def _state(positions: dict, **toplevel) -> dict:
    s = {"positions": positions, "last_processed_candle": {}, "last_dashboard_update": None}
    s.update(toplevel)
    return s


def test_merge_when_momentum_saves_preserves_funding_and_whale_from_disk():
    """Momentum bot saving must not clobber funding or whale positions on disk."""
    ours = _state({"BTC": {"entry_price": 80000, "quantity": "0.01"}})
    disk = _state({
        "BTC": {"entry_price": 79000, "quantity": "0.01"},  # stale momentum
        "WHALE_DOGE": {"entry_price": 0.1, "quantity": "5000"},
        "FUNDING_ETH": {"entry_price": 2400, "quantity": "0.5"},
    })

    merged = _merge_state(ours, disk, owner="momentum")

    assert merged["positions"]["BTC"]["entry_price"] == 80000  # our update wins
    assert "WHALE_DOGE" in merged["positions"]                 # whale preserved
    assert "FUNDING_ETH" in merged["positions"]                # funding preserved


def test_merge_when_whale_saves_preserves_funding_and_momentum_from_disk():
    ours = _state({"WHALE_DOGE": {"entry_price": 0.11, "quantity": "5000"}})
    disk = _state({
        "WHALE_DOGE": {"entry_price": 0.10, "quantity": "5000"},  # stale whale
        "BTC": {"entry_price": 80000, "quantity": "0.01"},
        "FUNDING_ETH": {"entry_price": 2400, "quantity": "0.5"},
    })

    merged = _merge_state(ours, disk, owner="whale")

    assert merged["positions"]["WHALE_DOGE"]["entry_price"] == 0.11  # our update
    assert "BTC" in merged["positions"]                              # momentum preserved
    assert "FUNDING_ETH" in merged["positions"]                      # funding preserved


def test_merge_when_funding_saves_preserves_momentum_and_whale_from_disk():
    """Critical: today's _merge_state has no 'funding' branch — this test will fail."""
    ours = _state({"FUNDING_ETH": {"entry_price": 2400, "quantity": "0.5"}})
    disk = _state({
        "FUNDING_ETH": {"entry_price": 2350, "quantity": "0.5"},  # stale funding
        "BTC": {"entry_price": 80000, "quantity": "0.01"},
        "WHALE_DOGE": {"entry_price": 0.1, "quantity": "5000"},
    })

    merged = _merge_state(ours, disk, owner="funding")

    assert merged["positions"]["FUNDING_ETH"]["entry_price"] == 2400  # our update
    assert "BTC" in merged["positions"]                               # momentum preserved
    assert "WHALE_DOGE" in merged["positions"]                        # whale preserved


def test_merge_when_funding_saves_does_not_inject_disk_funding_into_ours():
    """Funding save must drop any disk FUNDING_* keys that aren't in ours (those are stale)."""
    ours = _state({"FUNDING_ETH": {"entry_price": 2400, "quantity": "0.5"}})
    disk = _state({
        "FUNDING_ETH": {"entry_price": 2400, "quantity": "0.5"},
        "FUNDING_SOL": {"entry_price": 100, "quantity": "5"},  # stale, should be dropped
    })

    merged = _merge_state(ours, disk, owner="funding")

    assert "FUNDING_ETH" in merged["positions"]
    assert "FUNDING_SOL" not in merged["positions"]  # funding owner cleared this


# ─── find_most_profitable_position namespace filter ─────────────────────────

class FakeExecutor:
    def __init__(self, prices: dict):
        self.prices = prices

    def get_symbol_price(self, symbol):
        return self.prices.get(symbol)


def test_find_most_profitable_funding_owner_skips_momentum_and_whale():
    """Funding owner must only consider FUNDING_* positions."""
    state = _state({
        "BTC": {"entry_price": 80000, "quantity": "0.01", "symbol": "BTCUSDT",
                "direction": "LONG"},
        "WHALE_DOGE": {"entry_price": 0.1, "quantity": "5000", "symbol": "DOGEUSDT",
                       "direction": "SHORT"},
        "FUNDING_ETH": {"entry_price": 2400, "quantity": "0.5", "symbol": "ETHUSDT",
                        "direction": "LONG"},
    })
    # Momentum BTC is the most profitable in raw dollar terms, but funding owner
    # must skip it and return the FUNDING_ETH position.
    fake = FakeExecutor({"BTCUSDT": 85000, "DOGEUSDT": 0.05, "ETHUSDT": 2500})

    result = find_most_profitable_position(state, fake, owner="funding")
    assert result == "FUNDING_ETH"


def test_find_most_profitable_momentum_owner_skips_funding():
    """Momentum owner must skip FUNDING_* positions (today it incorrectly includes them)."""
    state = _state({
        "BTC": {"entry_price": 80000, "quantity": "0.01", "symbol": "BTCUSDT",
                "direction": "LONG"},
        "FUNDING_ETH": {"entry_price": 2400, "quantity": "0.5", "symbol": "ETHUSDT",
                        "direction": "LONG"},
    })
    # FUNDING_ETH would have higher uPnL ($50 vs $50 — same) but if both equal,
    # implementation order may pick either. Make FUNDING_ETH strictly higher so
    # the test is unambiguous: momentum must STILL pick BTC because it filters
    # FUNDING_ETH out.
    fake = FakeExecutor({"BTCUSDT": 80100, "ETHUSDT": 2500})

    result = find_most_profitable_position(state, fake, owner="momentum")
    assert result == "BTC"  # not FUNDING_ETH, despite ETH having higher PnL


def test_find_most_profitable_whale_owner_skips_funding():
    state = _state({
        "WHALE_DOGE": {"entry_price": 0.10, "quantity": "5000", "symbol": "DOGEUSDT",
                       "direction": "SHORT"},
        "FUNDING_ETH": {"entry_price": 2400, "quantity": "0.5", "symbol": "ETHUSDT",
                        "direction": "LONG"},
    })
    fake = FakeExecutor({"DOGEUSDT": 0.09, "ETHUSDT": 2500})

    result = find_most_profitable_position(state, fake, owner="whale")
    assert result == "WHALE_DOGE"


def test_find_most_profitable_returns_none_when_no_owned_positions():
    """When state has only other-owner positions, return None."""
    state = _state({
        "WHALE_DOGE": {"entry_price": 0.10, "quantity": "5000", "symbol": "DOGEUSDT",
                       "direction": "SHORT"},
        "FUNDING_ETH": {"entry_price": 2400, "quantity": "0.5", "symbol": "ETHUSDT",
                        "direction": "LONG"},
    })
    fake = FakeExecutor({"DOGEUSDT": 0.09, "ETHUSDT": 2500})

    result = find_most_profitable_position(state, fake, owner="momentum")
    assert result is None


# ─── Regression: existing two-way behavior still works ──────────────────────

def test_merge_existing_momentum_whale_behavior_unchanged():
    """Make sure the existing momentum/whale split still works exactly as before."""
    ours = _state({"BTC": {"entry_price": 80000, "quantity": "0.01"}})
    disk = _state({
        "BTC": {"entry_price": 79000, "quantity": "0.01"},
        "WHALE_DOGE": {"entry_price": 0.1, "quantity": "5000"},
    })

    merged = _merge_state(ours, disk, owner="momentum")

    assert merged["positions"]["BTC"]["entry_price"] == 80000
    assert "WHALE_DOGE" in merged["positions"]


def test_find_most_profitable_existing_momentum_whale_separation_unchanged():
    state = _state({
        "BTC": {"entry_price": 80000, "quantity": "0.01", "symbol": "BTCUSDT",
                "direction": "LONG"},
        "WHALE_DOGE": {"entry_price": 0.10, "quantity": "5000", "symbol": "DOGEUSDT",
                       "direction": "SHORT"},
    })
    fake = FakeExecutor({"BTCUSDT": 81000, "DOGEUSDT": 0.05})

    assert find_most_profitable_position(state, fake, owner="momentum") == "BTC"
    assert find_most_profitable_position(state, fake, owner="whale") == "WHALE_DOGE"
