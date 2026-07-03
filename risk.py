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


def bracket_trigger_price(entry_price: float, direction: str, reason,
                            cfg: dict,
                            default_sl_pct: float = 1.0,
                            default_tp_pct: float = 2.0) -> float | None:
    """P1.1 — paper-fidelity fill price for bracket exits.

    With exchange-resident TP/SL orders (attached at entry), a triggered
    exit fills at approximately the TRIGGER price, not at whatever price
    the bot's 60-second poll happens to observe afterwards. This helper
    maps (entry, direction, exit reason) to that trigger so DRY_RUN paper
    fills model the exchange-native architecture instead of the legacy
    poll-lag fills (which overshot brackets by up to 2x — worst observed
    -$2.02 on a 1% SL / $100 notional).

    Returns None for non-bracket exit reasons (signal flip, stale, manual)
    so callers fall back to the live polled price.
    """
    if not reason or not isinstance(reason, str):
        return None
    reason_l = reason.lower()
    if "sl" in reason_l and "hit" in reason_l:
        leg = "SL"
    elif "tp" in reason_l and "hit" in reason_l:
        leg = "TP"
    else:
        return None
    sl_pct = float(cfg.get("sl_pct", default_sl_pct))
    tp_pct = float(cfg.get("tp_pct", default_tp_pct))
    if direction == "LONG":
        return entry_price * (1 - sl_pct / 100.0) if leg == "SL" \
            else entry_price * (1 + tp_pct / 100.0)
    if direction == "SHORT":
        return entry_price * (1 + sl_pct / 100.0) if leg == "SL" \
            else entry_price * (1 - tp_pct / 100.0)
    return None


def risk_sized_qty(risk_usd: float, entry_price: float, stop_price: float,
                     max_notional_usd: float) -> float:
    """P3.2 — fixed-dollar risk sizing.

    qty = risk_usd / |entry - stop| so every stopped trade loses the same
    dollars regardless of volatility regime (community standard companion
    to ATR stops). Capped at max_notional_usd / entry so a tight stop
    can't balloon the position beyond the bot's margin x leverage budget.
    Returns 0.0 on degenerate inputs.
    """
    if risk_usd <= 0 or entry_price <= 0 or max_notional_usd <= 0:
        return 0.0
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        return 0.0
    qty = risk_usd / stop_distance
    max_qty = max_notional_usd / entry_price
    return round(min(qty, max_qty), 4)
