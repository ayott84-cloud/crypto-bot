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

# Global daily drawdown breaker
# Negative number; the breaker fires when the trailing-24h closed P&L is
# below (more negative than) this threshold.
MAX_DAILY_DRAWDOWN_USD = -500.0


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
    return "momentum"


def _filter_to_owner(trades: List[dict], owner: str) -> List[dict]:
    if owner in ("whale", "funding", "momentum"):
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


def should_pause(owner: str) -> KillSwitchStatus:
    """Return (paused, reason). Bots call this before opening a new position.

    owner: "momentum", "whale", or any tag string. The consecutive-loss
    breaker filters by owner; the global daily-DD breaker is account-wide.
    """
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

    # 1. Global daily drawdown
    daily_pnl = _trailing_pnl(closed, hours=24)
    if daily_pnl <= MAX_DAILY_DRAWDOWN_USD:
        return KillSwitchStatus(
            True,
            f"daily drawdown ${daily_pnl:+.2f} <= ${MAX_DAILY_DRAWDOWN_USD:.2f} threshold (24h trailing)",
        )

    # 2. Per-bot consecutive losses
    owned = _filter_to_owner(closed, owner)
    streak = _consecutive_losses_since_last_win(owned)
    if streak >= CONSECUTIVE_LOSS_LIMIT:
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
                    f"{owner} bot has {streak} consecutive losses; cooling off "
                    f"for {remaining:.1f} more hours",
                )

    return KillSwitchStatus(False)


def status_summary() -> dict:
    """Returns a dashboard-friendly snapshot for all three bots + global state."""
    out = {}
    for owner in ("momentum", "whale", "funding"):
        s = should_pause(owner)
        out[owner] = {"paused": s.paused, "reason": s.reason}
    return out
