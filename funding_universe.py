"""Funding-bot universe selection by OI.

Phase C.1 of the comprehensive enhancement plan. Replaces the top-100
market-cap filter (`whale_universe.get_top_symbols`) — which excluded
exactly the long-tail coins where funding extremes actually live
(HOMEUSDT at -2190% APR, VICUSDT at -815%, 1000BTTUSDT at -315% on
2026-05-22, all outside top-100 but well above any sensible OI floor).

This is a pure helper — operates on the HL `meta_and_ctxs` map already
fetched once per cycle. The WEEX whitelist intersection stays in the
caller's loop (funding_main.run_cycle) so this function only knows
about OI quality.
"""

from __future__ import annotations

from typing import Mapping


def is_fade_direction_enabled(direction: str, allow_long: bool, allow_short: bool) -> bool:
    """Phase C.3 direction toggle. Defensive default: unknown direction → False."""
    if direction == "LONG":
        return bool(allow_long)
    if direction == "SHORT":
        return bool(allow_short)
    return False


def get_perp_universe_by_oi(hl_ctx_map: Mapping, min_oi_usd: float) -> set[str]:
    """Return the set of coin names with `ctx.oi_usd >= min_oi_usd`.

    Duck-typed on `.oi_usd` so any object with that attribute works (real
    HLContext in production, FakeCtx in tests). Skips coins whose value is
    None / not present, rather than raising — degraded HL responses
    shouldn't crash the cycle.
    """
    out: set[str] = set()
    for coin, ctx in hl_ctx_map.items():
        if ctx is None:
            continue
        oi = getattr(ctx, "oi_usd", None)
        if oi is None:
            continue
        try:
            if float(oi) >= min_oi_usd:
                out.add(coin)
        except (TypeError, ValueError):
            continue
    return out
