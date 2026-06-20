"""Phase M.5 extension — Binance USDT-M futures historical kline fetcher.

WEEX's public kline endpoint (`/capi/v3/market/klines`) is hardcapped at
the most-recent 1000 bars per call with no startTime/endTime support. For
5-minute strategies this is only ~3.5 days, too short for the scalp
validator's n>=10+ trade threshold.

Binance's USDT-M futures klines (`/fapi/v1/klines`) allow up to 1500 bars
per call and accept startTime/endTime, so we can chain backward in time
to accumulate any window length. Since the top-10 perp prices are
arbitraged to within basis points between WEEX and Binance on 5m closes,
this is a clean backtest proxy.

This module is BACKTEST-ONLY. Live trading still routes through
executor.get_klines (WEEX). Importing this module from a live bot
would be an architecture smell.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger("crypto_bot.tools._binance_klines")

_BINANCE_FUTURES_BASE = "https://fapi.binance.com"
_KLINES_PATH = "/fapi/v1/klines"
_PER_CALL_LIMIT = 1500  # Binance's hard cap
_RATE_LIMIT_SLEEP_S = 0.25  # well under Binance's 2400 weight/min limit

# Binance accepts these directly — same strings as WEEX uses
_VALID_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w",
}

# Interval → milliseconds per bar
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
    """One HTTP call to Binance. Returns raw positional kline rows."""
    params = {
        "symbol":   symbol,
        "interval": interval,
        "limit":    str(limit),
    }
    if end_time_ms is not None:
        params["endTime"] = str(end_time_ms)
    try:
        r = requests.get(_BINANCE_FUTURES_BASE + _KLINES_PATH,
                          params=params, timeout=15.0)
        if r.status_code != 200:
            logger.warning("Binance klines HTTP %d for %s %s: %s",
                              r.status_code, symbol, interval, r.text[:200])
            return []
        return r.json() or []
    except (requests.RequestException, ValueError) as e:
        logger.warning("Binance klines request failed for %s %s: %s",
                          symbol, interval, e)
        return []


def fetch_klines_chained(symbol: str, interval: str, total_bars: int) -> list:
    """Paginate backward in time to accumulate `total_bars` of historical
    klines. Returns rows in CHRONOLOGICAL order (oldest first) to match
    the WEEX shape signals.build_dataframe expects.

    Implementation: walks BACKWARD from now in 1500-bar chunks. Each
    subsequent call's endTime = previous call's earliest open_time - 1ms,
    so we don't fetch the same bar twice.

    Bar format (Binance USDT-M futures, positional):
      [open_time_ms, open, high, low, close, volume,
       close_time_ms, quote_asset_vol, n_trades,
       taker_buy_base_vol, taker_buy_quote_vol, ignore]

    The first 6 columns match the WEEX layout exactly, so
    signals.build_dataframe handles them identically.
    """
    if interval not in _VALID_INTERVALS:
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
            break  # API failure or symbol unsupported — stop here
        # Prepend so the final array stays chronological after we reverse
        accumulated = chunk + accumulated
        # Walk backward — next call's endTime = oldest open_time - 1
        oldest_open_time = int(chunk[0][0])
        end_time_ms = oldest_open_time - 1
        remaining -= len(chunk)
        if len(chunk) < chunk_limit:
            # API returned fewer than asked — no more history available
            break
        time.sleep(_RATE_LIMIT_SLEEP_S)

    # Trim to requested size (the last chunk may have overshot)
    if len(accumulated) > total_bars:
        accumulated = accumulated[-total_bars:]

    return accumulated
