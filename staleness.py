"""Latency / staleness checks for upstream data sources.

A bot trading on stale data is worse than a bot not trading at all — the
position thesis may have already played out by the time we act. This module
gives both bots a single helper to verify their input data is fresh.

Two patterns:

1. **Timestamp check**: a record carries an absolute `timestamp_ms` field
   (e.g. HL leaderboard rows have last-update times). Use `is_fresh_ts(ts_ms,
   max_age_s)`.

2. **Wall-clock check**: a fetch was just performed and the bot wants to
   verify the response wasn't generated long ago. Use `StaleGuard` to wrap
   a fetch and bail if elapsed > N seconds (catches hung connections).

Both patterns log a warning rather than raising, by default. Caller decides
whether to skip the cycle or proceed.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger("crypto_bot.staleness")


def is_fresh_ts(timestamp_ms: Optional[int], max_age_s: float, *,
                source: str = "data", warn: bool = True) -> bool:
    """True if `timestamp_ms` (epoch milliseconds) is within `max_age_s` seconds of now.

    `None` or 0 is treated as stale.
    """
    if not timestamp_ms or timestamp_ms <= 0:
        if warn:
            logger.warning("Staleness check: %s has no timestamp", source)
        return False
    age_s = time.time() - (timestamp_ms / 1000.0)
    if age_s > max_age_s:
        if warn:
            logger.warning("Staleness check: %s is %.0fs old (max %.0fs)",
                           source, age_s, max_age_s)
        return False
    if age_s < -60:
        # Future timestamp by more than a minute — clock skew or bad data
        if warn:
            logger.warning("Staleness check: %s timestamp is %.0fs in the future "
                           "(clock skew? bad data?)", source, -age_s)
        return False
    return True


@contextmanager
def stale_guard(label: str, max_elapsed_s: float, *, warn: bool = True):
    """Wrap a fetch / external call. On exit, warn if it took too long.

    Usage:
        with stale_guard("HL leaderboard", max_elapsed_s=60):
            data = get_leaderboard()
        # If data took > 60s to arrive, we logged a warning — caller may
        # decide to skip downstream logic that depends on freshness.
    """
    t0 = time.time()
    try:
        yield
    finally:
        elapsed = time.time() - t0
        if elapsed > max_elapsed_s and warn:
            logger.warning("Stale guard: %s took %.1fs (threshold %.0fs) — "
                           "downstream data may be from a stale request",
                           label, elapsed, max_elapsed_s)


def cohort_freshness_ratio(records: list, ts_field: str = "timestamp_ms",
                           max_age_s: float = 600) -> float:
    """Fraction of `records` whose timestamp is within `max_age_s` seconds.

    Useful for cohort fetches (e.g. 20 whale wallets — what fraction returned
    fresh data?). Returns 0.0 on empty input.
    """
    if not records:
        return 0.0
    fresh = sum(1 for r in records if is_fresh_ts(r.get(ts_field, 0),
                                                   max_age_s, warn=False))
    return fresh / len(records)
