"""Unit tests for whale_signals.classify() and aggregate_cohort().

Run: python -m pytest tests/test_whale_signals.py -v
Or:  python tests/test_whale_signals.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Add parent (crypto_bot/) to path so we can import whale_signals
HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

from whale_signals import (
    CoinStats, classify, aggregate_cohort, hl_coin_to_weex_symbol,
    extract_liq_data, compute_liq_context,
    build_position_snapshot, compute_recency,
    enrich_signal,
    LiqContext, RecencyContext,
    DIVERGENCE_LONG, DIVERGENCE_SHORT, CONSENSUS_LONG, CONSENSUS_SHORT,
)
from whale_hl_data import HLContext
from whale_universe import set_top_symbols_for_test


@pytest.fixture(autouse=True)
def _stub_top100():
    """Deterministic top-100 for tests — bypasses CoinGecko network call."""
    set_top_symbols_for_test({
        "BTC", "ETH", "SOL", "XRP", "DOGE", "LINK", "AVAX", "LTC", "ADA",
        "NEAR", "AAVE", "SUI", "FIL", "APT", "ARB", "PEPE", "BONK", "HYPE",
        "WLFI", "LIT", "ZEC", "FOO", "ZRO",
    })
    yield


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_stats(coin: str, longs: int, shorts: int,
               long_notional: float = 0.0, short_notional: float = 0.0,
               upnl: float = 100.0) -> CoinStats:
    return CoinStats(
        coin=coin, longs=longs, shorts=shorts,
        long_notional=long_notional, short_notional=short_notional,
        upnl_sum=upnl,
    )


# ─── Classify: positive cases ────────────────────────────────────────────────

def test_divergence_long_fires():
    """Smart 80% long + rekt 80% short + winning → DIVERGENCE_LONG."""
    smart = make_stats("SOL", longs=8, shorts=2, long_notional=5_000_000,
                        short_notional=500_000, upnl=50_000)
    rekt = make_stats("SOL", longs=2, shorts=8)
    sig = classify("SOL", smart, rekt, "SOLUSDT")
    assert sig is not None
    assert sig.signal == DIVERGENCE_LONG
    assert sig.direction == "LONG"
    assert sig.confidence >= 5


def test_divergence_short_fires():
    smart = make_stats("LIT", longs=1, shorts=9, long_notional=50_000,
                        short_notional=8_000_000, upnl=1_500_000)
    rekt = make_stats("LIT", longs=9, shorts=1)
    sig = classify("LIT", smart, rekt, "LITUSDT")
    assert sig is not None
    assert sig.signal == DIVERGENCE_SHORT
    assert sig.direction == "SHORT"


def test_consensus_long_fires():
    """Smart ≥80% long with no special rekt positioning → CONSENSUS_LONG."""
    smart = make_stats("WLFI", longs=8, shorts=1, long_notional=2_000_000, upnl=25_000)
    rekt = make_stats("WLFI", longs=3, shorts=3)  # no opposite bias
    sig = classify("WLFI", smart, rekt, "WLFIUSDT")
    assert sig is not None
    assert sig.signal == CONSENSUS_LONG


def test_consensus_short_fires():
    """Smart ≥85% short → CONSENSUS_SHORT (new tightened threshold)."""
    smart = make_stats("ZEC", longs=1, shorts=10, short_notional=3_000_000, upnl=10_000)
    rekt = make_stats("ZEC", longs=4, shorts=4)
    sig = classify("ZEC", smart, rekt, "ZECUSDT")
    assert sig is not None
    assert sig.signal == CONSENSUS_SHORT


# ─── Classify: guards ────────────────────────────────────────────────────────

def test_min_traders_floor():
    """Fewer than 5 smart traders → no signal."""
    smart = make_stats("FOO", longs=3, shorts=1)
    rekt = make_stats("FOO", longs=0, shorts=0)
    assert classify("FOO", smart, rekt, "FOOUSDT") is None


def test_edge_decay_guard_blocks_losing_basket():
    """Smart is 90% short but UNDERWATER → skip (they're losing on this trade)."""
    smart = make_stats("HYPE", longs=1, shorts=9, short_notional=5_000_000, upnl=-1_650_000)
    rekt = make_stats("HYPE", longs=0, shorts=0)
    assert classify("HYPE", smart, rekt, "HYPEUSDT") is None


def test_crowded_long_trade_skipped():
    """Smart ≥70% long AND rekt ≥70% long → crowded, skip."""
    smart = make_stats("BTC", longs=8, shorts=2, long_notional=20_000_000, upnl=100_000)
    rekt = make_stats("BTC", longs=8, shorts=2)
    assert classify("BTC", smart, rekt, "BTCUSDT") is None


def test_crowded_short_trade_skipped():
    smart = make_stats("DOGE", longs=1, shorts=9, short_notional=3_000_000, upnl=50_000)
    rekt = make_stats("DOGE", longs=1, shorts=9)
    assert classify("DOGE", smart, rekt, "DOGEUSDT") is None


def test_divergence_requires_rekt_population():
    """Divergence needs at least 3 rekt traders — 2 isn't enough."""
    smart = make_stats("XRP", longs=8, shorts=1, long_notional=3_000_000, upnl=30_000)
    rekt = make_stats("XRP", longs=0, shorts=2)  # only 2 rekt traders
    sig = classify("XRP", smart, rekt, "XRPUSDT")
    # smart_long_pct is 89%, rekt_short_pct is 100% but n=2 — still falls through
    # to CONSENSUS_LONG because smart ≥ 80%.
    assert sig is not None
    assert sig.signal == CONSENSUS_LONG


def test_consensus_threshold_too_low():
    """75% smart long is above 70 (divergence) but below 80 (consensus); without
    rekt opposition, should NOT fire."""
    smart = make_stats("ADA", longs=6, shorts=2, long_notional=1_000_000, upnl=5_000)
    rekt = make_stats("ADA", longs=4, shorts=4)
    # smart_long_pct = 75%, not >=80, and rekt not >=70 short → NONE
    assert classify("ADA", smart, rekt, "ADAUSDT") is None


# ─── Divergence bonus & scoring ──────────────────────────────────────────────

def test_divergence_scores_higher_than_consensus():
    """Same smart positioning, but divergence beats consensus on score."""
    smart = make_stats("AVAX", longs=9, shorts=1, long_notional=4_000_000, upnl=50_000)
    rekt_flat = make_stats("AVAX", longs=4, shorts=4)
    rekt_opposite = make_stats("AVAX", longs=0, shorts=8)
    s_cons = classify("AVAX", smart, rekt_flat, "AVAXUSDT")
    s_div = classify("AVAX", smart, rekt_opposite, "AVAXUSDT")
    assert s_cons is not None and s_div is not None
    assert s_div.signal.startswith("DIVERGENCE")
    assert s_cons.signal.startswith("CONSENSUS")
    assert s_div.score > s_cons.score


# ─── aggregate_cohort ────────────────────────────────────────────────────────

def test_aggregate_cohort_counts_and_sums():
    wallets = [
        {"positions": [
            {"coin": "BTC", "direction": "LONG", "size": 10, "entry_price": 78000, "unrealized_pnl": 500},
            {"coin": "ETH", "direction": "SHORT", "size": 100, "entry_price": 2300, "unrealized_pnl": -200},
        ]},
        {"positions": [
            {"coin": "BTC", "direction": "LONG", "size": 5, "entry_price": 78000, "unrealized_pnl": 100},
            {"coin": "BTC", "direction": "SHORT", "size": 1, "entry_price": 78000, "unrealized_pnl": 50},
        ]},
    ]
    stats = aggregate_cohort(wallets)
    assert stats["BTC"].longs == 2
    assert stats["BTC"].shorts == 1
    assert stats["BTC"].long_notional == pytest.approx(15 * 78000)
    assert stats["BTC"].short_notional == pytest.approx(1 * 78000)
    assert stats["BTC"].upnl_sum == pytest.approx(650)
    assert stats["ETH"].shorts == 1
    assert stats["ETH"].short_notional == pytest.approx(100 * 2300)


# ─── Symbol mapping ──────────────────────────────────────────────────────────

def test_hl_coin_to_weex_direct():
    """Regular coins: append USDT."""
    assert hl_coin_to_weex_symbol("BTC") == "BTCUSDT"
    assert hl_coin_to_weex_symbol("sol") == "SOLUSDT"


def test_hl_coin_to_weex_k_prefix_override():
    """kPEPE → PEPEUSDT (price-per-1000 convention is HL-only)."""
    assert hl_coin_to_weex_symbol("kPEPE") == "PEPEUSDT"
    assert hl_coin_to_weex_symbol("kBONK") == "BONKUSDT"


def test_hl_coin_to_weex_whitelist_filter():
    """Not in whitelist → None."""
    whitelist = {"BTCUSDT", "ETHUSDT"}
    assert hl_coin_to_weex_symbol("BTC", whitelist) == "BTCUSDT"
    assert hl_coin_to_weex_symbol("FART", whitelist) is None


# ─── Top-100 market-cap filter ───────────────────────────────────────────────

def test_top100_allows_major():
    """Coins in the stubbed top-100 pass through."""
    assert hl_coin_to_weex_symbol("BTC") == "BTCUSDT"
    assert hl_coin_to_weex_symbol("DOGE") == "DOGEUSDT"


def test_top100_blocks_longtail_coin():
    """Coin NOT in the top-100 stub → None (illiquid long-tail rejected)."""
    # MOODENG (or any random micro-cap) isn't in the stub
    assert hl_coin_to_weex_symbol("MOODENG") is None


def test_top100_allows_k_prefix_coin_when_base_in_top():
    """kPEPE normalizes to PEPE; if PEPE is top-100, kPEPE is allowed."""
    # PEPE is in the stub
    assert hl_coin_to_weex_symbol("kPEPE") == "PEPEUSDT"


def test_top100_blocks_k_prefix_coin_when_base_not_in_top():
    """kFOOBAR normalizes to FOOBAR; if FOOBAR isn't top-100, block."""
    assert hl_coin_to_weex_symbol("kFOOBAR") is None


def test_top100_bypass_works_for_backtest():
    """check_top100=False skips the filter — used by backtest + internal code paths."""
    # Even though MOODENG isn't in top-100, bypass returns the candidate symbol
    assert hl_coin_to_weex_symbol("MOODENG", check_top100=False) == "MOODENGUSDT"


# ─── Tier 1: Liquidation cluster extraction & math ───────────────────────────

def test_extract_liq_data_skips_positions_without_liq_price():
    wallets = [{"address": "0xabc", "positions": [
        {"coin": "BTC", "direction": "LONG", "size": 1.0, "entry_price": 70000, "liquidation_price": 60000},
        {"coin": "ETH", "direction": "LONG", "size": 10.0, "entry_price": 2300, "liquidation_price": None},
    ]}]
    liq = extract_liq_data(wallets)
    assert "BTC" in liq and len(liq["BTC"]) == 1
    assert liq["BTC"][0][0] == 60000.0  # liq price
    assert "ETH" not in liq  # skipped (no liq price)


def test_liq_context_long_adverse_cluster_below():
    """LONG at $100 with 3 whale LONG liqs at $95-97 → adverse cluster detected."""
    smart_liq = {"TEST": [
        (95.0, 1_000_000, "LONG"),
        (96.0, 500_000, "LONG"),
        (97.0, 2_000_000, "LONG"),
    ]}
    rekt_liq = {}
    ctx = compute_liq_context("TEST", "LONG", current_price=100.0,
                               smart_liq_data=smart_liq, rekt_liq_data=rekt_liq)
    assert ctx.adverse_notional_usd == 3_500_000
    assert ctx.fuel_notional_usd == 0
    # Nearest adverse is 97, distance = 3/100 = 0.03
    assert ctx.adverse_nearest_pct == pytest.approx(0.03, abs=0.001)


def test_liq_context_long_fuel_cluster_above():
    """LONG at $100 with SHORT liqs above = fuel if price rises."""
    smart_liq = {"TEST": [
        (103.0, 2_000_000, "SHORT"),
        (105.0, 1_000_000, "SHORT"),
    ]}
    ctx = compute_liq_context("TEST", "LONG", current_price=100.0,
                               smart_liq_data=smart_liq, rekt_liq_data={})
    assert ctx.fuel_notional_usd == 3_000_000
    assert ctx.adverse_notional_usd == 0


def test_liq_context_short_mirror():
    """SHORT at $100 with SHORT liqs above = adverse (squeeze risk). LONG liqs below = fuel."""
    smart_liq = {"TEST": [
        (102.0, 5_000_000, "SHORT"),   # adverse for our short
        (95.0, 2_000_000, "LONG"),     # fuel — cascade accelerates
    ]}
    ctx = compute_liq_context("TEST", "SHORT", current_price=100.0,
                               smart_liq_data=smart_liq, rekt_liq_data={})
    assert ctx.adverse_notional_usd == 5_000_000
    assert ctx.fuel_notional_usd == 2_000_000
    assert ctx.adverse_nearest_pct == pytest.approx(0.02, abs=0.001)


def test_liq_context_ignores_liqs_outside_window():
    """LONG at $100, a LONG liq at $70 is too far to matter (outside 6% window)."""
    smart_liq = {"TEST": [(70.0, 1_000_000, "LONG")]}
    ctx = compute_liq_context("TEST", "LONG", current_price=100.0,
                               smart_liq_data=smart_liq, rekt_liq_data={})
    assert ctx.adverse_notional_usd == 0


# ─── Tier 1: Recency diff ────────────────────────────────────────────────────

def test_recency_detects_new_entry():
    prev = {"0xA": {}, "0xB": {}}
    curr = {"0xA": {"BTC_LONG": 100_000}, "0xB": {"BTC_LONG": 50_000}}
    r = compute_recency("BTC", "LONG", prev, curr)
    assert r.new_count == 2
    assert r.growth_count == 0


def test_recency_detects_growth_and_shrink():
    prev = {"0xA": {"BTC_LONG": 100_000}, "0xB": {"BTC_LONG": 100_000}}
    curr = {"0xA": {"BTC_LONG": 150_000}, "0xB": {"BTC_LONG": 80_000}}
    r = compute_recency("BTC", "LONG", prev, curr)
    # 10% threshold: 50% growth counts, 20% shrink counts
    assert r.growth_count == 1
    assert r.shrink_count == 1


def test_recency_ignores_small_noise():
    """5% position change is below the 10% noise floor — no growth flagged."""
    prev = {"0xA": {"BTC_LONG": 100_000}}
    curr = {"0xA": {"BTC_LONG": 105_000}}
    r = compute_recency("BTC", "LONG", prev, curr)
    assert r.growth_count == 0
    assert r.shrink_count == 0


def test_recency_detects_full_exit():
    prev = {"0xA": {"BTC_LONG": 100_000}, "0xB": {"BTC_LONG": 50_000}}
    curr = {"0xA": {}, "0xB": {}}
    r = compute_recency("BTC", "LONG", prev, curr)
    assert r.exit_count == 2


def test_build_position_snapshot_keys_by_coin_direction():
    wallets = [{"address": "0xA", "positions": [
        {"coin": "BTC", "direction": "LONG", "size": 1.0, "entry_price": 70000},
        {"coin": "BTC", "direction": "SHORT", "size": 0.5, "entry_price": 70000},
        {"coin": "ETH", "direction": "LONG", "size": 10.0, "entry_price": 2300},
    ]}]
    snap = build_position_snapshot(wallets)
    assert "0xA" in snap
    assert "BTC_LONG" in snap["0xA"]
    assert "BTC_SHORT" in snap["0xA"]
    assert "ETH_LONG" in snap["0xA"]
    assert snap["0xA"]["BTC_LONG"] == pytest.approx(70000.0)


# ─── Tier 1: enrich_signal confidence/score adjustments ──────────────────────

def _sample_signal(direction="LONG", confidence=5, score=50, net_notional=1_000_000):
    """Build a minimal signal for enrichment testing."""
    from whale_signals import WhaleSignal
    return WhaleSignal(
        coin="TEST", weex_symbol="TESTUSDT", signal="CONSENSUS_LONG",
        direction=direction, score=score, confidence=confidence,
        smart_long_pct=90 if direction == "LONG" else 10,
        smart_short_pct=10 if direction == "LONG" else 90,
        smart_n=8, smart_net_notional=net_notional, smart_upnl_sum=50_000,
        rekt_long_pct=50, rekt_short_pct=50, rekt_n=5, reasoning="base",
    )


def test_enrich_funding_confirms_long():
    """LONG + negative funding (shorts paying) = confirmation bonus."""
    sig = _sample_signal(direction="LONG", confidence=5)
    hl_ctx = HLContext(coin="TEST", funding_rate=-0.0002, open_interest=1,
                        mark_price=100, premium=0, oi_usd=100)
    enrich_signal(sig, hl_ctx=hl_ctx)
    assert sig.confidence == 6
    assert "confirms" in sig.reasoning


def test_enrich_funding_penalizes_crowded_long():
    """LONG + extreme positive funding = crowded, penalty."""
    sig = _sample_signal(direction="LONG", confidence=7)
    hl_ctx = HLContext(coin="TEST", funding_rate=0.0008, open_interest=1,
                        mark_price=100, premium=0, oi_usd=100)
    enrich_signal(sig, hl_ctx=hl_ctx)
    assert sig.confidence == 6
    assert "crowded" in sig.reasoning


def test_enrich_liq_adverse_cluster_penalty():
    """Adverse cluster > 2× conviction AND < 3% away = heavy penalty."""
    sig = _sample_signal(confidence=7, net_notional=1_000_000)
    liq = LiqContext(coin="TEST", direction="LONG", current_price=100,
                     adverse_notional_usd=3_000_000,  # 3× conviction
                     fuel_notional_usd=0,
                     adverse_nearest_pct=0.02)        # 2% away
    enrich_signal(sig, liq=liq)
    assert sig.confidence == 5  # -2
    assert "adverse liq" in sig.reasoning


def test_enrich_liq_fuel_bonus():
    sig = _sample_signal(confidence=5, net_notional=1_000_000)
    liq = LiqContext(coin="TEST", direction="LONG", current_price=100,
                     adverse_notional_usd=0,
                     fuel_notional_usd=3_000_000,
                     adverse_nearest_pct=None)
    enrich_signal(sig, liq=liq)
    assert sig.confidence == 6


def test_enrich_recency_new_entries_bonus():
    sig = _sample_signal(confidence=5)
    rec = RecencyContext(coin="TEST", direction="LONG", new_count=3)
    enrich_signal(sig, recency=rec)
    assert sig.confidence == 6
    assert "3 fresh entries" in sig.reasoning


def test_enrich_recency_mass_exit_penalty():
    sig = _sample_signal(confidence=7)
    rec = RecencyContext(coin="TEST", direction="LONG", exit_count=4)
    enrich_signal(sig, recency=rec)
    assert sig.confidence == 5  # -2
    assert "exiting" in sig.reasoning


def test_enrich_stacks_multiple_signals():
    """All Tier 1 bonuses combine: funding + fuel + recency = confidence capped at 10."""
    sig = _sample_signal(confidence=7, net_notional=1_000_000)
    hl_ctx = HLContext(coin="TEST", funding_rate=-0.0002, open_interest=1,
                        mark_price=100, premium=0, oi_usd=100)
    liq = LiqContext(coin="TEST", direction="LONG", current_price=100,
                     adverse_notional_usd=0, fuel_notional_usd=5_000_000,
                     adverse_nearest_pct=None)
    rec = RecencyContext(coin="TEST", direction="LONG", new_count=3, growth_count=2)
    enrich_signal(sig, liq=liq, hl_ctx=hl_ctx, recency=rec)
    # +1 funding, +1 fuel, +1 new, +1 growth = +4, capped at 10 from 7 → 10 (max)
    # but exact stacking: 7 + 1 + 1 + 1 + 1 = 11, clamped to 10
    assert sig.confidence == 10
    assert sig.enrichment_applied is True


def test_enrich_confidence_floor_at_1():
    sig = _sample_signal(confidence=2)
    rec = RecencyContext(coin="TEST", direction="LONG", exit_count=5)  # -2
    hl_ctx = HLContext(coin="TEST", funding_rate=0.001, open_interest=1,
                        mark_price=100, premium=0, oi_usd=100)         # -1 (crowded)
    enrich_signal(sig, hl_ctx=hl_ctx, recency=rec)
    # 2 - 2 - 1 = -1, floored at 1
    assert sig.confidence == 1


# ─── CLI fallback for manual run ─────────────────────────────────────────────

if __name__ == "__main__":
    # Minimal runner so we can sanity-check even without pytest installed
    import traceback
    tests = [fn for name, fn in globals().items() if name.startswith("test_") and callable(fn)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"[PASS] {t.__name__}")
            passed += 1
        except Exception:
            print(f"[FAIL] {t.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
