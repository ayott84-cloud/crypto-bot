"""Kill switches — circuit breakers checked before every new entry.

Two layers:

1. **Per-bot consecutive-loss breaker.** If a bot logs N losses in a row
   without an intervening win, pause that bot for COOLOFF_HOURS.

2. **Global daily-drawdown breaker.** If account-wide closed P&L over the
   trailing 24 hours is below MAX_DAILY_DRAWDOWN_USD, pause all bots until
   midnight UTC. (Reads closed trades from the journal — works in DRY_RUN
   against paper P&L, works in LIVE against real P&L.)

Both checks read directly from the trade journal so there's no extra state
file to keep in sync. The bot's "should I open?" path calls
`should_pause(bot_owner)` and respects the result.

Usage from main.py / whale_main.py:

    from kill_switch import should_pause

    paused, reason = should_pause("momentum")
    if paused:
        logger.warning("Kill-switch active: %s — skipping new entries", reason)
        return  # Existing positions still manage to exit
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

logger = logging.getLogger("crypto_bot.kill_switch")


# ─── Tunables (override via .env in config.py if desired) ──────────────────

# Per-bot consecutive-loss breaker
CONSECUTIVE_LOSS_LIMIT = 5
CONSECUTIVE_LOSS_COOLOFF_HOURS = 24

# Phase E.3: tighter per-direction limit for SHORT trades. Crypto shorts have
# unbounded loss potential (price → ∞); cooler-headed limit during validation.
CONSECUTIVE_LOSS_LIMIT_SHORT = 2

# Global daily drawdown breaker
# Negative number; the breaker fires when the trailing-24h closed P&L is
# below (more negative than) this threshold.
MAX_DAILY_DRAWDOWN_USD = -500.0

# P3.6: percent-of-capital daily breaker. 3% of INITIAL_CAPITAL ($5000 →
# -$150) is far tighter than the legacy fixed floor; the effective
# threshold is whichever of the two is TIGHTER (less negative). Set to
# None to disable and fall back to the fixed-USD floor alone.
MAX_DAILY_DRAWDOWN_PCT = 3.0


def _daily_dd_threshold_usd() -> float:
    """Effective daily-drawdown threshold: tighter of fixed-USD and
    percent-of-capital. Degrades to the fixed floor if config import
    fails or the percent knob is disabled."""
    if MAX_DAILY_DRAWDOWN_PCT is None:
        return MAX_DAILY_DRAWDOWN_USD
    try:
        from config import INITIAL_CAPITAL
    except ImportError:
        return MAX_DAILY_DRAWDOWN_USD
    pct_threshold = -(MAX_DAILY_DRAWDOWN_PCT / 100.0) * float(INITIAL_CAPITAL)
    return max(MAX_DAILY_DRAWDOWN_USD, pct_threshold)


@dataclass
class KillSwitchStatus:
    paused: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.paused


def _bot_of(strategy: str) -> str:
    if not isinstance(strategy, str):
        return "momentum"
    if strategy.startswith("Whale Track"):
        return "whale"
    if strategy.startswith("Funding Fade"):
        return "funding"
    if strategy.startswith("Pair "):
        # Legacy prefix form ("Pair ETHBTC") written before Jul 16 2026 —
        # evaded the endswith chain, charging pair losses to momentum.
        return "pair"
    # Per-asset names are like "BTC 5m Scalp" / "ETH 1h Crossover" /
    # "BTC 4H Breakout" / "ETHBTC Pair" / "BTC 1D Reversal". The bare
    # tags ("Scalp", "Crossover", "Breakout", "Pair", "Reversal") are
    # the fallback when strategy_name is missing. endswith covers both
    # shapes without false-positiving against momentum names like
    # "BTC 1D Momentum".
    if strategy.endswith("Scalp"):
        return "scalp"
    if strategy.endswith("Crossover"):
        return "crossover"
    if strategy.endswith("Breakout"):
        return "breakout"
    if strategy.endswith("Pair"):
        return "pair"
    if strategy.endswith("Reversal"):
        return "reversal"
    return "momentum"


# Owners recognized by the per-owner consecutive-loss filter. Trades whose
# _bot_of() classification falls outside this set are treated as 'global'
# (return everything) when should_pause() is called with an unfamiliar owner.
_RECOGNIZED_OWNERS = (
    "whale", "funding", "momentum",
    "scalp", "crossover", "breakout", "pair", "reversal",
)


def _filter_to_owner(trades: List[dict], owner: str) -> List[dict]:
    if owner in _RECOGNIZED_OWNERS:
        return [t for t in trades if _bot_of(t.get("strategy", "")) == owner]
    return trades  # 'global' / unknown owner — return everything


def _parse_close_time(t: dict) -> Optional[datetime]:
    raw = t.get("date_closed")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        # Drop tzinfo for comparison with naive datetimes
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except (TypeError, ValueError):
        return None


def _consecutive_losses_since_last_win(closed: List[dict]) -> int:
    """Count losses at the END of the closed-trade list (most recent first wins reset)."""
    streak = 0
    # Iterate from most recent closed trade backwards
    for t in reversed(closed):
        result = t.get("result")
        if result == "WIN":
            break
        if result == "LOSS":
            streak += 1
        # FLAT trades neither break nor extend the streak
    return streak


def _trailing_pnl(closed: List[dict], hours: int) -> float:
    cutoff = datetime.now() - timedelta(hours=hours)
    total = 0.0
    for t in closed:
        ct = _parse_close_time(t)
        if ct is None or ct < cutoff:
            continue
        total += float(t.get("net_pnl") or 0)
    return total


def _last_win_time(closed: List[dict]) -> Optional[datetime]:
    for t in reversed(closed):
        if t.get("result") == "WIN":
            return _parse_close_time(t)
    return None


def should_pause(owner: str, direction: str | None = None) -> KillSwitchStatus:
    """Return (paused, reason). Bots call this before opening a new position.

    owner: "momentum", "whale", or any tag string. The consecutive-loss
    breaker filters by owner; the global daily-DD breaker is account-wide.

    direction: optional "LONG" or "SHORT". When set, only same-direction
    trades count toward the consecutive-loss streak, and the SHORT side
    uses a tighter limit (CONSECUTIVE_LOSS_LIMIT_SHORT=2) since crypto
    shorts have unbounded loss potential. None = original behavior
    (all-direction streak vs CONSECUTIVE_LOSS_LIMIT=5).
    """
    # 0. R3 — operator pause via the Discord control plane. Checked
    # FIRST (cheap file read, no journal I/O) so a Discord "!pause X"
    # takes effect on the very next entry check.
    try:
        from control_flags import is_operator_paused
        if is_operator_paused(owner):
            return KillSwitchStatus(
                True, f"operator pause for {owner} (Discord control plane)")
    except Exception:  # noqa: BLE001 — flags problems never block trading
        pass

    try:
        from journal import read_trades  # local import to avoid module-level cycles
    except ImportError:
        return KillSwitchStatus(False)

    try:
        all_trades = read_trades(max_rows=1000)
    except Exception as e:
        logger.warning("Kill-switch unable to read journal: %s — defaulting to NOT paused", e)
        return KillSwitchStatus(False)

    closed = [t for t in all_trades if t.get("result") in ("WIN", "LOSS")]

    # 1. Global daily drawdown — tighter of fixed-USD and %-of-capital (P3.6)
    daily_pnl = _trailing_pnl(closed, hours=24)
    dd_threshold = _daily_dd_threshold_usd()
    if daily_pnl <= dd_threshold:
        return KillSwitchStatus(
            True,
            f"daily drawdown ${daily_pnl:+.2f} <= ${dd_threshold:.2f} threshold (24h trailing)",
        )

    # 2. Per-bot consecutive losses (optionally direction-filtered)
    owned = _filter_to_owner(closed, owner)
    if direction in ("LONG", "SHORT"):
        owned = [t for t in owned if (t.get("direction") or "").upper() == direction]
        limit = CONSECUTIVE_LOSS_LIMIT_SHORT if direction == "SHORT" else CONSECUTIVE_LOSS_LIMIT
        label = f"{owner}-{direction}"
    else:
        limit = CONSECUTIVE_LOSS_LIMIT
        label = owner

    streak = _consecutive_losses_since_last_win(owned)
    if streak >= limit:
        # Check if cooloff has elapsed since the LAST loss
        last_loss = None
        for t in reversed(owned):
            if t.get("result") == "LOSS":
                last_loss = _parse_close_time(t)
                break
        if last_loss is not None:
            elapsed_hours = (datetime.now() - last_loss).total_seconds() / 3600
            if elapsed_hours < CONSECUTIVE_LOSS_COOLOFF_HOURS:
                remaining = CONSECUTIVE_LOSS_COOLOFF_HOURS - elapsed_hours
                return KillSwitchStatus(
                    True,
                    f"{label} bot has {streak} consecutive losses; cooling off "
                    f"for {remaining:.1f} more hours",
                )

    return KillSwitchStatus(False)


def status_summary() -> dict:
    """Returns a dashboard-friendly snapshot for all bots + global state."""
    out = {}
    for owner in ("momentum", "whale", "funding",
                    "scalp", "crossover", "breakout", "pair", "reversal"):
        s = should_pause(owner)
        out[owner] = {"paused": s.paused, "reason": s.reason}
    return out
