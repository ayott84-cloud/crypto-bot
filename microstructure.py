"""Tier 0.2 — microstructure helpers.

A SpreadTracker keeps a per-symbol rolling window of bid-ask spread samples
(in basis points) and exposes an `is_air_pocket(symbol, multiplier)` gate
that scalp + crossover use to skip entries during liquidity dislocations.

Design notes:
  - Spreads stored in BPS (basis points = price / mid * 10000) so the gate
    threshold is unit-agnostic across symbols at different price scales.
  - Default window=60 samples (one cycle per minute = one hour of history
    for scalp; for crossover at 1h cadence each sample is an hour, so a
    60-sample window gives ~2.5 days of context).
  - Default min_samples=10 before the gate can return True — prevents
    cold-start false positives.
  - Graceful degradation: when in doubt, ALLOW the entry. The gate
    only blocks when there is positive evidence of an air pocket.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Deque, Dict, Optional

logger = logging.getLogger("crypto_bot.microstructure")


# ─── SpreadTracker ─────────────────────────────────────────────────────────

class SpreadTracker:
    """Per-symbol rolling history of bid-ask spreads in basis points.

    Caller is responsible for fetching the spread sample (see
    fetch_spread_bps below) and feeding it via add_sample. The gate logic
    lives in is_air_pocket.
    """

    DEFAULT_WINDOW = 60
    DEFAULT_MIN_SAMPLES = 10
    DEFAULT_AIR_POCKET_MULT = 3.0

    def __init__(self, window: int = DEFAULT_WINDOW,
                   min_samples: int = DEFAULT_MIN_SAMPLES) -> None:
        self._window = window
        self._min_samples = min_samples
        self._samples: Dict[str, Deque[float]] = {}

    def add_sample(self, symbol: str, bps: float) -> None:
        if symbol not in self._samples:
            self._samples[symbol] = deque(maxlen=self._window)
        self._samples[symbol].append(float(bps))

    def sample_count(self, symbol: str) -> int:
        return len(self._samples.get(symbol, ()))

    def current_bps(self, symbol: str) -> Optional[float]:
        q = self._samples.get(symbol)
        if not q:
            return None
        return q[-1]

    def rolling_mean_bps(self, symbol: str) -> Optional[float]:
        q = self._samples.get(symbol)
        if not q:
            return None
        return sum(q) / len(q)

    def is_air_pocket(self, symbol: str,
                       multiplier: float = DEFAULT_AIR_POCKET_MULT) -> bool:
        """True if current spread > multiplier × rolling mean AND we have
        enough samples to trust the comparison. False on cold-start or any
        missing data."""
        count = self.sample_count(symbol)
        if count < self._min_samples:
            return False
        mean = self.rolling_mean_bps(symbol)
        current = self.current_bps(symbol)
        if mean is None or current is None or mean <= 0:
            # Degenerate (zero mean): treat as "no signal, allow entry"
            return False
        return current > multiplier * mean


# ─── fetch_spread_bps — adapter over executor.get_ticker_24h ───────────────

def fetch_spread_bps(executor, symbol: str) -> Optional[float]:
    """Pull the latest bid/ask snapshot for `symbol` via WEEX's ticker24h
    endpoint and compute the spread in basis points.

    Returns None on:
      - network exception (gate degrades to allow entry)
      - empty ticker response
      - missing bidPrice / askPrice fields
      - inverted or zero quotes (crossed market / stale snapshot)
    """
    try:
        rows = executor.get_ticker_24h(symbol)
    except Exception as e:  # noqa: BLE001
        logger.debug("[%s] fetch_spread_bps: ticker fetch failed: %s", symbol, e)
        return None

    if not rows:
        return None

    # ticker24h returns a list; first entry is what we want
    row = rows[0] if isinstance(rows, list) else rows
    if not isinstance(row, dict):
        return None

    bid_raw = row.get("bidPrice")
    ask_raw = row.get("askPrice")
    if bid_raw is None or ask_raw is None:
        return None

    try:
        bid = float(bid_raw)
        ask = float(ask_raw)
    except (TypeError, ValueError):
        return None

    if bid <= 0 or ask <= 0 or ask <= bid:
        # zero quote or crossed market — bail
        return None

    mid = (bid + ask) / 2.0
    spread = ask - bid
    return (spread / mid) * 10_000.0  # bps


def fetch_all_spreads_bps(executor) -> Dict[str, float]:
    """Batch variant — one ticker24h(None) call returns ALL symbols.
    Returns {symbol: bps} for rows that pass the same defensive checks
    as fetch_spread_bps. Malformed rows are silently skipped.

    Used by the per-cycle entry path so the bot pays one API call per
    cycle rather than one per asset.
    """
    try:
        rows = executor.get_ticker_24h(None)
    except Exception as e:  # noqa: BLE001
        logger.debug("fetch_all_spreads_bps: ticker fetch failed: %s", e)
        return {}

    if not isinstance(rows, list):
        return {}

    out: Dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol")
        if not symbol:
            continue
        bid_raw = row.get("bidPrice")
        ask_raw = row.get("askPrice")
        if bid_raw is None or ask_raw is None:
            continue
        try:
            bid = float(bid_raw)
            ask = float(ask_raw)
        except (TypeError, ValueError):
            continue
        if bid <= 0 or ask <= 0 or ask <= bid:
            continue
        mid = (bid + ask) / 2.0
        spread = ask - bid
        out[symbol] = (spread / mid) * 10_000.0
    return out


# ─── Process-level singleton (so bots can share one tracker per process) ──

_DEFAULT_TRACKER: Optional[SpreadTracker] = None


def get_default_tracker() -> SpreadTracker:
    """Return the per-process default SpreadTracker. Each systemd-managed
    bot runs in its own Python process and therefore gets its own tracker —
    they do not share state across processes."""
    global _DEFAULT_TRACKER
    if _DEFAULT_TRACKER is None:
        _DEFAULT_TRACKER = SpreadTracker()
    return _DEFAULT_TRACKER
