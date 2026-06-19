"""Phase L.3.2 — volatility-adaptive position sizing.

Single pure helper consumed by every bot's entry path. Reduces margin
during high-vol regimes so per-trade dollar risk stays roughly stable
across regime transitions. Per the L.3 design table, each bot passes
its OWN base margin (Momentum MARGIN_PER_TRADE, Whale
WHALE_MARGIN_CONSENSUS, Funding FUNDING_MARGIN_USD, Pair
PAIR_MARGIN_PER_LEG, Reversal REVERSAL_MARGIN_PER_TRADE, Breakout
BREAKOUT_MARGIN_PER_TRADE) — the helper doesn't need to know which bot
is calling.

Scaling table:
    vol_regime == "high"      → 0.7×   (ATR above SMA — wider expected
                                          per-trade swing)
    vol_regime == "low"       → 1.0×   (calm — full base)
    vol_regime == "unknown"   → 1.0×   (no scaling under uncertainty)

vol_regime comes from regime.classify_from_df()["vol"] — already the
canonical source the L.2 regime gate uses. Reading it here ensures the
gate verdict and the sizing decision agree.
"""

from __future__ import annotations


# Scale by vol bucket. Easy to tune later; tests assert these multipliers.
_VOL_MULTIPLIERS = {
    "high":    0.7,
    "low":     1.0,
    "unknown": 1.0,
}


def vol_scaled_margin(base_margin: float, vol_regime: str | None) -> float:
    """Scale a bot's base margin by the current vol regime.

    The classifier only returns "high" / "low" / "unknown" — any other
    string falls back to 1.0×, intentionally — we want the helper to
    fail-safe (no scaling) rather than fail-closed (zero margin) if a
    new regime label is added upstream.
    """
    if base_margin <= 0:
        return 0.0
    multiplier = _VOL_MULTIPLIERS.get(vol_regime, 1.0)
    return base_margin * multiplier


def is_high_vol(vol_regime: str | None) -> bool:
    """Convenience predicate for dashboard surfacing + logs."""
    return vol_regime == "high"
