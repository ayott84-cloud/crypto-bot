"""Phase W.C — whale cohort quality scoring.

Replaces 90d-PnL ranking (survivorship bias prone) with a composite
score rewarding consistency over raw spikes:

  score = 0.5 * normalized_sharpe
        + 0.3 * normalized_pnl
        + 0.2 * normalized_longevity

Pure functions. Caller fetches the underlying HL leaderboard data and
passes wallets as dicts; this module ranks them.

References:
  - whale.ag — Sharpe/Calmar-ranked trader leaderboards
  - Caleb Koome Medium 2026 — survivorship-bias critique
"""

from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger("crypto_bot.whale_cohort")


# Default weights (sum to 1.0). Override per backtest.
WEIGHT_SHARPE   = 0.5
WEIGHT_PNL      = 0.3
WEIGHT_LONGEVITY = 0.2

DEFAULT_MIN_DAYS_ACTIVE = 180
DEFAULT_MIN_COMPOSITE_SCORE = 0.4


def longevity_qualifies(days_active: Optional[int],
                         min_days: int = DEFAULT_MIN_DAYS_ACTIVE) -> bool:
    """Eliminates "lucky three-month winners" — must have ≥180d of activity."""
    if days_active is None or days_active < 0:
        return False
    return days_active >= min_days


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def compute_wallet_score(wallet_data: dict, normalizers: dict) -> float:
    """Composite score on the [0, 1] interval.

    wallet_data fields (all optional except presence):
      - sharpe:        float — wallet's realized Sharpe over the lookback
      - pnl_90d_usd:   float — 90-day realized PnL
      - days_active:   int   — days since first trade

    normalizers (caller computes from the full leaderboard):
      - max_sharpe:    e.g. 99th percentile cohort Sharpe
      - max_pnl:       99th percentile 90d PnL
      - max_days:      cap (typically 365)
    """
    sharpe = float(wallet_data.get("sharpe") or 0.0)
    pnl    = float(wallet_data.get("pnl_90d_usd") or 0.0)
    days   = float(wallet_data.get("days_active") or 0.0)

    max_sharpe = float(normalizers.get("max_sharpe") or 1.0)
    max_pnl    = float(normalizers.get("max_pnl") or 1.0)
    max_days   = float(normalizers.get("max_days") or 365.0)

    n_sharpe = _clamp01(sharpe / max_sharpe) if max_sharpe > 0 else 0.0
    n_pnl    = _clamp01(pnl    / max_pnl)    if max_pnl    > 0 else 0.0
    n_days   = _clamp01(days   / max_days)   if max_days   > 0 else 0.0

    score = (WEIGHT_SHARPE * n_sharpe
             + WEIGHT_PNL    * n_pnl
             + WEIGHT_LONGEVITY * n_days)
    return _clamp01(score)


def filter_qualifying_wallets(
    wallets: List[dict],
    min_score: float = DEFAULT_MIN_COMPOSITE_SCORE,
    min_days: int = DEFAULT_MIN_DAYS_ACTIVE,
    normalizers: Optional[dict] = None,
) -> List[dict]:
    """Returns the subset of wallets that pass BOTH the score gate AND
    the longevity gate. Computes normalizers from the input set if not
    provided.
    """
    if not wallets:
        return []

    if normalizers is None:
        sharpes = [float(w.get("sharpe") or 0) for w in wallets]
        pnls    = [float(w.get("pnl_90d_usd") or 0) for w in wallets]
        days    = [float(w.get("days_active") or 0) for w in wallets]
        normalizers = {
            "max_sharpe": max(sharpes) or 1.0,
            "max_pnl":    max(pnls) or 1.0,
            "max_days":   max(days) or 365.0,
        }

    out = []
    for w in wallets:
        if not longevity_qualifies(w.get("days_active"), min_days=min_days):
            continue
        if compute_wallet_score(w, normalizers) < min_score:
            continue
        out.append(w)
    return out
