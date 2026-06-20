"""Phase M.5 extension — historical kline fetcher for extended-window backtests.

WEEX's public kline endpoint is hardcapped at the most-recent 1000 bars
per call with no startTime/endTime support — for a 5-minute strategy this
is only ~3.5 days, too short for the scalp validator's n threshold.

This module fetches from Bybit V5 (linear perpetual klines) instead.
Bybit:
  - Public endpoint, no auth required
  - 1000 bars per call
  - start/end parameters for chained pagination
  - NOT geo-blocked from DigitalOcean US (unlike Binance, which returns
    HTTP 451 to DO US IPs per their terms-of-service eligibility check)
  - Same symbols as WEEX (BTCUSDT, ETHUSDT, etc.) on USDT perps
  - Prices arbitraged tight to WEEX on 5m closes — clean backtest proxy

Filename retained from the original Binance attempt to avoid churning
all the import paths; the implementation underneath is now Bybit.

This module is BACKTEST-ONLY. Live trading still routes through
executor.get_klines (WEEX).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger("crypto_bot.tools._binance_klines")

_BYBIT_BASE = "https://api.bybit.com"
_KLINES_PATH = "/v5/market/kline"
_PER_CALL_LIMIT = 1000
_RATE_LIMIT_SLEEP_S = 0.15  # well under Bybit's 600 req / 5s limit

# Bybit's V5 API uses minutes for short intervals + letter codes for daily+
_INTERVAL_BYBIT = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D", "3d": "D",  # 3d not native; D returned, caller dedups
    "1w": "W",
}

_INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "12h": 43_200_000,
    "1d": 86_400_000, "3d": 259_200_000, "1w": 604_800_000,
}


def _interval_ms(interval: str) -> int:
    ms = _INTERVAL_MS.get(interval.lower())
    if ms is None:
        raise ValueError(f"Unsupported interval: {interval}")
    return ms


def _one_call(symbol: str, interval: str, end_time_ms: Optional[int],
                limit: int) -> list:
    """One HTTP call to Bybit V5 linear kline endpoint.

    Returns rows in WEEX-positional shape: [open_time_ms, open, high,
    low, close, volume, ...]. Bybit returns NEWEST first within a single
    call; we reverse here so the caller always sees chronological order
    within each chunk.
    """
    bybit_interval = _INTERVAL_BYBIT.get(interval.lower())
    if bybit_interval is None:
        raise ValueError(f"Unsupported interval for Bybit: {interval}")
    params = {
        "category": "linear",
        "symbol":   symbol,
        "interval": bybit_interval,
        "limit":    str(limit),
    }
    if end_time_ms is not None:
        params["end"] = str(end_time_ms)
    try:
        r = requests.get(_BYBIT_BASE + _KLINES_PATH,
                          params=params, timeout=15.0)
        if r.status_code != 200:
            logger.warning("Bybit klines HTTP %d for %s %s: %s",
                              r.status_code, symbol, interval, r.text[:200])
            return []
        payload = r.json() or {}
        ret_code = payload.get("retCode", -1)
        if ret_code != 0:
            logger.warning("Bybit klines retCode=%s for %s %s: %s",
                              ret_code, symbol, interval,
                              payload.get("retMsg", "")[:200])
            return []
        result = payload.get("result", {}) or {}
        rows = result.get("list", []) or []
        # Convert each row to numeric open_time + string OHLCV so the
        # rest of the pipeline (which expects WEEX positional rows) works.
        # Bybit row shape: [startTime, open, high, low, close, volume, turnover]
        converted = []
        for row in rows:
            try:
                converted.append([int(row[0]), row[1], row[2], row[3],
                                    row[4], row[5]])
            except (IndexError, TypeError, ValueError):
                continue
        # Bybit returns newest-first within a single call. Reverse for
        # chronological order within the chunk.
        converted.reverse()
        return converted
    except (requests.RequestException, ValueError) as e:
        logger.warning("Bybit klines request failed for %s %s: %s",
                          symbol, interval, e)
        return []


def fetch_klines_chained(symbol: str, interval: str, total_bars: int) -> list:
    """Paginate backward in time to accumulate `total_bars` of historical
    klines from Bybit V5. Returns rows in CHRONOLOGICAL order (oldest
    first) to match the WEEX shape signals.build_dataframe expects.

    Implementation: walks BACKWARD from now in 1000-bar chunks. Each
    subsequent call's `end` parameter = previous call's earliest
    open_time - 1ms, so we don't fetch the same bar twice.
    """
    if interval.lower() not in _INTERVAL_BYBIT:
        raise ValueError(f"Unsupported interval: {interval}")
    if total_bars <= 0:
        return []

    accumulated: list = []
    end_time_ms: Optional[int] = None  # first call: most recent bars
    remaining = total_bars

    while remaining > 0:
        chunk_limit = min(_PER_CALL_LIMIT, remaining)
        chunk = _one_call(symbol, interval, end_time_ms, chunk_limit)
        if not chunk:
            break
        # Prepend so the final array stays chronological after we stop
        accumulated = chunk + accumulated
        oldest_open_time = int(chunk[0][0])
        end_time_ms = oldest_open_time - 1
        remaining -= len(chunk)
        if len(chunk) < chunk_limit:
            # API returned fewer than asked — no more history available
            break
        time.sleep(_RATE_LIMIT_SLEEP_S)

    if len(accumulated) > total_bars:
        accumulated = accumulated[-total_bars:]

    return accumulated
