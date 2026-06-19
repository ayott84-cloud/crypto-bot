"""Phase L.2 — regime gate tests.

Covers:
  - classify_from_df handles missing ema_200 column (the silent-no-op
    risk the peer-review flagged)
  - gate_blocks_direction matrix
  - Per-asset config flag wiring
  - blocker_labels coverage for "regime_misalign"
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

import regime


# ─── gate_blocks_direction matrix ─────────────────────────────────────────

def test_gate_blocks_long_during_strong_down():
    assert regime.gate_blocks_direction("strong_down", "LONG") is True


def test_gate_blocks_short_during_strong_up():
    assert regime.gate_blocks_direction("strong_up", "SHORT") is True


def test_gate_passes_aligned_directions():
    assert regime.gate_blocks_direction("strong_up", "LONG") is False
    assert regime.gate_blocks_direction("strong_down", "SHORT") is False


def test_gate_passes_weak_trends():
    assert regime.gate_blocks_direction("weak_up", "LONG") is False
    assert regime.gate_blocks_direction("weak_down", "LONG") is False
    assert regime.gate_blocks_direction("weak_up", "SHORT") is False
    assert regime.gate_blocks_direction("weak_down", "SHORT") is False


def test_gate_passes_ranges():
    assert regime.gate_blocks_direction("range_high_vol", "LONG") is False
    assert regime.gate_blocks_direction("range_low_vol", "SHORT") is False


def test_gate_passes_unknown_label():
    """unknown is the silent-no-op state; gate must not block under
    uncertainty."""
    assert regime.gate_blocks_direction("unknown", "LONG") is False
    assert regime.gate_blocks_direction("unknown", "SHORT") is False


# ─── classify_from_df: missing ema_200 (silent no-op risk) ───────────────

def _build_df(n: int = 220) -> "pd.DataFrame":
    """Build a synthetic DF with the columns signals.compute_indicators
    typically produces (ema_fast, ema_slow, atr, atr_sma, adx) but NO
    ema200 column — the gap the peer review flagged."""
    closes = [100.0 + i * 0.5 for i in range(n)]  # gently rising
    df = pd.DataFrame({"close": closes})
    df["ema_fast"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=50, adjust=False).mean()
    df["atr"]     = 1.0
    df["atr_sma"] = 0.8
    df["adx"]     = 25.0
    return df


def test_classify_from_df_computes_missing_ema200():
    """classify_from_df must compute ema_200 inline rather than silently
    returning unknown."""
    df = _build_df(n=220)
    assert "ema200" not in df.columns
    result = regime.classify_from_df(df, cfg={})
    # With a rising series + ADX=25, ema_fast > ema_slow > ema_200 + close
    # rising → label should be a real trend label, not "unknown"
    assert result["label"] != "unknown"


def test_classify_from_df_returns_unknown_when_too_short():
    """Less than 200 bars means ema_200 can't form — caller's gate becomes
    a benign no-op."""
    df = _build_df(n=50)
    result = regime.classify_from_df(df, cfg={})
    assert result["label"] == "unknown"


def test_classify_from_df_does_not_mutate_input():
    """Adding ema_200 must not mutate the caller's DataFrame."""
    df = _build_df(n=220)
    _ = regime.classify_from_df(df, cfg={})
    assert "ema200" not in df.columns


def test_classify_from_df_uses_ema_fast_slow_columns():
    """Bot DataFrames have ema_fast/ema_slow, not ema20/ema50. The wrapper
    must read either."""
    df = _build_df(n=220)
    # ema_fast > ema_slow > ema_200 with rising close + ADX 25
    result = regime.classify_from_df(df, cfg={})
    assert result["trend"] == "up"
    assert result["strength"] == "strong"
    assert result["label"] == "strong_up"


# ─── Config flag wiring ───────────────────────────────────────────────────

def test_phase_k_momentum_promotions_have_regime_gate_on():
    from config import ASSETS, _MOMENTUM_PROMOTIONS
    promoted_keys = {name for name, _s, _i, _bts in _MOMENTUM_PROMOTIONS}
    for key in promoted_keys:
        cfg = ASSETS.get(key)
        if cfg is None:
            continue
        assert cfg.get("use_regime_gate") is True, (
            f"Phase K promotion {key} should have use_regime_gate=True")


def test_legacy_momentum_assets_do_not_have_regime_gate():
    """Legacy ASSETS rows must NOT have the gate enabled — their behavior
    is established and we don't want to alter signal counts mid-flight.
    Each can be flipped individually after backtest."""
    from config import ASSETS, _MOMENTUM_PROMOTIONS
    promoted_keys = {name for name, _s, _i, _bts in _MOMENTUM_PROMOTIONS}
    for key, cfg in ASSETS.items():
        if key in promoted_keys:
            continue
        assert cfg.get("use_regime_gate", False) is False, (
            f"legacy asset {key} should NOT have use_regime_gate set")


def test_phase_k_breakout_assets_have_regime_gate_on():
    """All Phase K breakout promotions (1H + D20 + Phase K candidates via
    factory) should default the gate ON."""
    from breakout_config import BREAKOUT_ASSETS
    # Phase K promotions: keys ending in _1H or _D20
    for key, cfg in BREAKOUT_ASSETS.items():
        if key.endswith("_1H") or key.endswith("_D20"):
            assert cfg.get("use_regime_gate") is True, (
                f"Phase K breakout asset {key} should have use_regime_gate=True")


def test_legacy_breakout_assets_do_not_have_regime_gate():
    """The 3 original 4H breakout assets (BTC/ETH/SOL) keep the flag off."""
    from breakout_config import BREAKOUT_ASSETS
    for key in ("BTC_4H", "ETH_4H", "SOL_4H"):
        if key in BREAKOUT_ASSETS:
            assert BREAKOUT_ASSETS[key].get("use_regime_gate", False) is False, (
                f"legacy breakout asset {key} should NOT have use_regime_gate")


# ─── Blocker label coverage ────────────────────────────────────────────

def test_regime_misalign_has_blocker_label():
    from blocker_labels import BLOCKER_LABELS, blocker_label
    assert "regime_misalign" in BLOCKER_LABELS
    label = blocker_label("regime_misalign")
    assert label
    assert label != "regime_misalign"  # not just echoing the key
