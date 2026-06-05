"""Unit tests for funding_universe.get_perp_universe_by_oi.

Phase C.1 of the comprehensive enhancement plan. Replaces the top-100
market-cap universe filter with an OI-floor filter so the funding bot
sees the coins where funding extremes actually live (HOMEUSDT at -2190%
APR, VICUSDT at -815%, 1000BTTUSDT at -315% — all outside top-100 but
inside any reasonable OI threshold).

Pure function — duck-typed on `.oi_usd` so tests don't need to construct
full HLContext objects.

Run: python -m pytest tests/test_funding_universe.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

from funding_universe import get_perp_universe_by_oi


class FakeCtx:
    """Minimal duck-typed stand-in for whale_hl_data.HLContext."""
    def __init__(self, oi_usd):
        self.oi_usd = oi_usd


def test_includes_coins_with_oi_above_threshold():
    ctx_map = {
        "BTC":  FakeCtx(100_000_000),   # $100M, included
        "ETH":  FakeCtx( 25_000_000),   # $25M,  included
        "HOME": FakeCtx(      1_000),   # $1k,   excluded
    }
    assert get_perp_universe_by_oi(ctx_map, min_oi_usd=20_000_000) == {"BTC", "ETH"}


def test_excludes_coins_below_threshold():
    ctx_map = {
        "HOME": FakeCtx(1_000),
        "VIC":  FakeCtx(50_000),
    }
    assert get_perp_universe_by_oi(ctx_map, min_oi_usd=20_000_000) == set()


def test_boundary_at_exact_threshold_is_included():
    """min_oi_usd is inclusive — >= comparison."""
    ctx_map = {"X": FakeCtx(20_000_000)}
    assert get_perp_universe_by_oi(ctx_map, min_oi_usd=20_000_000) == {"X"}


def test_empty_ctx_map_returns_empty():
    assert get_perp_universe_by_oi({}, min_oi_usd=20_000_000) == set()


def test_none_context_value_is_skipped():
    """Some HL responses may include a coin with no context; skip rather than crash."""
    ctx_map = {
        "BTC":  FakeCtx(100_000_000),
        "GHOST": None,
    }
    assert get_perp_universe_by_oi(ctx_map, min_oi_usd=20_000_000) == {"BTC"}


def test_missing_oi_usd_attribute_is_skipped():
    """A malformed ctx object (no oi_usd) is skipped, not raises."""
    class NoOI:
        pass
    ctx_map = {
        "BTC":   FakeCtx(100_000_000),
        "BROKE": NoOI(),
    }
    assert get_perp_universe_by_oi(ctx_map, min_oi_usd=20_000_000) == {"BTC"}


def test_zero_threshold_includes_all_real_contexts():
    """min_oi_usd=0 should match every non-None ctx with a numeric oi_usd."""
    ctx_map = {
        "BTC":  FakeCtx(100_000_000),
        "TINY": FakeCtx(1),
        "ZERO": FakeCtx(0),
        "GONE": None,
    }
    out = get_perp_universe_by_oi(ctx_map, min_oi_usd=0)
    assert out == {"BTC", "TINY", "ZERO"}


def test_extreme_funding_coins_outside_top100_are_included_when_oi_qualifies():
    """The real motivation: HOMEUSDT etc had $20M+ OI but weren't top-100 cap.

    This test documents intent — the function doesn't know about top-100,
    that's exactly the point.
    """
    ctx_map = {
        "HOME": FakeCtx(25_000_000),
        "VIC":  FakeCtx(30_000_000),
    }
    assert get_perp_universe_by_oi(ctx_map, min_oi_usd=20_000_000) == {"HOME", "VIC"}
