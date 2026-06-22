"""Phase W.B — whale signal filter stack.

Four pure-function filters that gate signals AFTER classify() produces them.
Each takes the relevant state and returns (passed: bool, reason: str).
Reason is empty when passed.

Composition: apply_filter_stack runs all four and reports the full set of
failures so the dashboard can show WHY a signal was suppressed.

References:
  - Vishal Menon, "Allure and Pitfalls of Tracking Smart Money" (2025)
  - whale.ag — Sharpe/Calmar-ranked leaderboards
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple, List

try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore

logger = logging.getLogger("crypto_bot.whale_filters")


# Defaults — tune via whale_config.py overrides if needed
FUNDING_LONG_BLOCK_THRESHOLD = 0.0003     # +0.03% / 8h or more = crowded LONG
FUNDING_SHORT_BLOCK_THRESHOLD = -0.0003   # -0.03% / 8h or less = crowded SHORT
PERSISTENCE_MIN_POLLS = 16                # 4 hours at 15-min polls
REGIME_BLOCK_LONG = ("strong_down",)
REGIME_BLOCK_SHORT = ("strong_up",)


def check_multi_tf_trend(direction: str, df_1d) -> Tuple[bool, str]:
    """1D EMA20 vs EMA50 trend gate.

    LONG passes when 1D ema_fast > ema_slow on the latest bar.
    SHORT passes when 1D ema_fast < ema_slow.
    Missing data → pass (don't block).
    """
    if df_1d is None or pd is None or len(df_1d) == 0:
        return True, ""
    if "ema_fast" not in df_1d.columns or "ema_slow" not in df_1d.columns:
        return True, ""
    last = df_1d.iloc[-1]
    ef = float(last["ema_fast"])
    es = float(last["ema_slow"])
    if direction == "LONG":
        if ef > es:
            return True, ""
        return False, f"1D trend down (ema_fast {ef:.2f} ≤ ema_slow {es:.2f})"
    if direction == "SHORT":
        if ef < es:
            return True, ""
        return False, f"1D trend up (ema_fast {ef:.2f} ≥ ema_slow {es:.2f})"
    return True, ""


def check_funding_sanity(direction: str,
                          funding_rate_8h: Optional[float]) -> Tuple[bool, str]:
    """Block signals where the cohort is taking a side that's already crowded
    in the funding market.

    LONG blocked when funding > +0.03%/8h (longs paying shorts means longs
    are crowded — whales late to the party).
    SHORT blocked when funding < -0.03%/8h.
    Missing rate → pass.
    """
    if funding_rate_8h is None:
        return True, ""
    if direction == "LONG" and funding_rate_8h >= FUNDING_LONG_BLOCK_THRESHOLD:
        return False, (f"crowded long: funding {funding_rate_8h*100:.4f}%/8h "
                        f">= {FUNDING_LONG_BLOCK_THRESHOLD*100:.3f}% threshold")
    if direction == "SHORT" and funding_rate_8h <= FUNDING_SHORT_BLOCK_THRESHOLD:
        return False, (f"crowded short: funding {funding_rate_8h*100:.4f}%/8h "
                        f"<= {FUNDING_SHORT_BLOCK_THRESHOLD*100:.3f}% threshold")
    return True, ""


def check_regime_gate(direction: str,
                       regime_label: Optional[str]) -> Tuple[bool, str]:
    """Phase B.3 regime classifier gate.

    Block LONG when regime is strong_down — fighting the macro tape.
    Block SHORT when regime is strong_up.
    All other regimes (range, weak, neutral) pass freely.
    Missing label → pass.
    """
    if not regime_label:
        return True, ""
    if direction == "LONG" and regime_label in REGIME_BLOCK_LONG:
        return False, f"regime gate: {regime_label} blocks LONG"
    if direction == "SHORT" and regime_label in REGIME_BLOCK_SHORT:
        return False, f"regime gate: {regime_label} blocks SHORT"
    return True, ""


def check_arkham_flow_gate(
    direction: str,
    net_flow_usd_24h: Optional[float],
    threshold_usd: float = 1_000_000.0,
) -> Tuple[bool, str]:
    """Phase W.E.2 — Arkham CEX-flow gate.

    Blocks LONG when top on-chain entities are NET DISTRIBUTORS (selling)
    above the threshold over the trailing 24h — they're exiting the
    position right as our cohort signal would have us enter.

    Blocks SHORT when top entities are NET ACCUMULATORS above threshold —
    we'd be shorting into a wave of on-chain buying.

    Aligned flow (LONG + accumulation, SHORT + distribution) is treated
    as confirmation: pass without comment.
    Missing data (None net_flow) → pass.
    """
    if net_flow_usd_24h is None:
        return True, ""

    if direction == "LONG" and net_flow_usd_24h < -threshold_usd:
        return False, (
            f"Arkham 24h net DISTRIBUTION ${net_flow_usd_24h:+,.0f} "
            f"< -${threshold_usd:,.0f} — top entities exiting position"
        )
    if direction == "SHORT" and net_flow_usd_24h > threshold_usd:
        return False, (
            f"Arkham 24h net ACCUMULATION ${net_flow_usd_24h:+,.0f} "
            f"> +${threshold_usd:,.0f} — top entities accumulating"
        )
    return True, ""


def check_persistence(coin: str, direction: str,
                       persistence_state: dict,
                       current_cycle: int,
                       min_polls: int = PERSISTENCE_MIN_POLLS) -> Tuple[bool, str]:
    """The same (coin, direction) signal must have appeared in at least
    `min_polls` of the recent poll cycles before we trust it.

    Filters flash-flips — whales who open and close within a single
    poll window aren't a conviction signal.

    persistence_state is a dict keyed by (coin, direction) with values
    {"poll_count": int, "last_seen_cycle": int, "first_cycle": int}.
    """
    key = (coin, direction)
    entry = persistence_state.get(key)
    if not entry:
        return False, f"persistence: first occurrence (need {min_polls} polls)"
    poll_count = int(entry.get("poll_count", 0))
    if poll_count < min_polls:
        return False, (f"persistence: seen {poll_count}/{min_polls} polls "
                        f"({current_cycle - entry.get('first_cycle', current_cycle)} cycles)")
    return True, ""


def apply_filter_stack(
    coin: str, direction: str,
    df_1d=None, funding_rate_8h: Optional[float] = None,
    regime_label: Optional[str] = None,
    persistence_state: Optional[dict] = None,
    current_cycle: int = 0,
) -> Tuple[bool, List[str]]:
    """Run all four filters; collect every failure reason (don't short-circuit).

    Returns (all_passed, [list of failure reasons]).
    """
    reasons: List[str] = []
    for ok, reason in (
        check_multi_tf_trend(direction, df_1d),
        check_funding_sanity(direction, funding_rate_8h),
        check_regime_gate(direction, regime_label),
        check_persistence(coin, direction, persistence_state or {},
                          current_cycle=current_cycle),
    ):
        if not ok and reason:
            reasons.append(reason)
    return (len(reasons) == 0, reasons)


def update_persistence_state(
    persistence_state: dict,
    current_signals: list,
    current_cycle: int,
) -> dict:
    """Update the persistence tracker after each poll.

    - For each (coin, direction) in current_signals, increment poll_count
    - For (coin, direction) NOT in current_signals, drop the entry
      (signal expired between polls — restart the counter on next appearance)

    Each `current_signals` entry is expected to expose `.coin` and `.direction`
    attributes (matching the WhaleSignal dataclass shape).
    """
    new_state: dict = {}
    current_keys = set()
    for sig in current_signals:
        coin = getattr(sig, "coin", None) or (sig.get("coin") if isinstance(sig, dict) else None)
        direction = getattr(sig, "direction", None) or (sig.get("direction") if isinstance(sig, dict) else None)
        if not coin or not direction:
            continue
        key = (coin, direction)
        current_keys.add(key)
        prev = persistence_state.get(key, {})
        new_state[key] = {
            "poll_count":     int(prev.get("poll_count", 0)) + 1,
            "first_cycle":    int(prev.get("first_cycle", current_cycle)),
            "last_seen_cycle": current_cycle,
        }
    return new_state
