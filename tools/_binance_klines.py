"""Phase M.5 extension — historical kline fetcher for extended-window backtests.

WEEX's public kline endpoint is hardcapped at the most-recent 1000 bars
per call with no startTime/endTime support — for a 5-minute strategy
this is only ~3.5 days, too short for the scalp validator's n threshold.

This module fetches from Coinbase Exchange (a US-licensed exchange whose
public klines endpoint is NOT geo-blocked from DigitalOcean US IPs).
Previous attempts: Binance returned HTTP 451 (terms-of-service
eligibility check), Bybit returned HTTP 403 (CloudFront geo-block).
Both block US datacenter IPs by policy.

Coinbase notes:
  - Public endpoint, no auth required
  - 300 bars per call (smaller than Binance/Bybit's 1000-1500)
  - start/end parameters (ISO 8601) for chained pagination
  - Spot-pair prices (BTC-USD, ETH-USD, etc.) — arbitraged tight to
    WEEX BTCUSDT perp on 5m closes; clean backtest proxy
  - WEEX symbols map: BTCUSDT → BTC-USD, etc.
  - BNB, TRX, ADA-PERP not Coinbase-listed; those symbols return empty

Filename retained to avoid churning all import paths; implementation
underneath is now Coinbase. Module is BACKTEST-ONLY — live trading
still routes through executor.get_klines (WEEX).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger("crypto_bot.tools._binance_klines")

_COINBASE_BASE = "https://api.exchange.coinbase.com"
_KLINES_PATH = "/products/{product_id}/candles"
_PER_CALL_LIMIT = 300        # Coinbase's hard cap per call
_RATE_LIMIT_SLEEP_S = 0.15   # Coinbase public 10 req/s — well under

# Coinbase uses granularity in SECONDS. Allowed: 60, 300, 900, 3600, 21600, 86400
_INTERVAL_GRANULARITY = {
    "1m": 60, "5m": 300, "15m": 900,
    "1h": 3600, "6h": 21600, "1d": 86400,
}

# Bar-duration in milliseconds (for chaining math)
_INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "12h": 43_200_000,
    "1d": 86_400_000, "3d": 259_200_000, "1w": 604_800_000,
}

# WEEX → Coinbase symbol map. Add new mappings here when expanding scalp
# universe. Coinbase has no BNB, TRX, or some smaller alts (delisted from
# US-licensed venues); those return empty and the validator will skip.
_SYMBOL_MAP = {
    "BTCUSDT":  "BTC-USD",
    "ETHUSDT":  "ETH-USD",
    "SOLUSDT":  "SOL-USD",
    "XRPUSDT":  "XRP-USD",
    "ADAUSDT":  "ADA-USD",
    "DOGEUSDT": "DOGE-USD",
    "AVAXUSDT": "AVAX-USD",
    "LINKUSDT": "LINK-USD",
}


def _interval_ms(interval: str) -> int:
    ms = _INTERVAL_MS.get(interval.lower())
    if ms is None:
        raise ValueError(f"Unsupported interval: {interval}")
    return ms


def _weex_to_coinbase(symbol: str) -> Optional[str]:
    """Translate a WEEX symbol to its Coinbase product_id. Returns None
    when no Coinbase listing exists (BNB, TRX) — caller should treat as
    "not available."""
    return _SYMBOL_MAP.get(symbol.upper())


def _one_call(symbol: str, interval: str, end_time_ms: Optional[int],
                limit: int) -> list:
    """One HTTP call to Coinbase Exchange candles endpoint.

    Returns rows in WEEX-positional shape: [open_time_ms, open, high,
    low, close, volume]. Coinbase returns NEWEST first in each chunk;
    we reverse here so the caller always sees chronological order.
    """
    product_id = _weex_to_coinbase(symbol)
    if product_id is None:
        logger.info("Symbol %s not on Coinbase; returning empty", symbol)
        return []

    granularity = _INTERVAL_GRANULARITY.get(interval.lower())
    if granularity is None:
        raise ValueError(f"Unsupported interval for Coinbase: {interval}")

    # Coinbase wants ISO 8601 timestamps for start/end. Compute the
    # `start` based on (end_time_ms minus `limit` × granularity).
    if end_time_ms is None:
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    else:
        end_ms = int(end_time_ms)
    start_ms = end_ms - (limit * granularity * 1000)

    params = {
        "granularity": str(granularity),
        "start":       datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat(),
        "end":         datetime.fromtimestamp(end_ms   / 1000, tz=timezone.utc).isoformat(),
    }

    try:
        r = requests.get(_COINBASE_BASE + _KLINES_PATH.format(product_id=product_id),
                          params=params, timeout=15.0,
                          headers={"User-Agent": "crypto-bot-backtest/1.0"})
        if r.status_code != 200:
            logger.warning("Coinbase klines HTTP %d for %s %s: %s",
                              r.status_code, symbol, interval, r.text[:200])
            return []
        rows = r.json() or []
        if not isinstance(rows, list):
            logger.warning("Coinbase unexpected payload for %s %s: %s",
                              symbol, interval, str(rows)[:200])
            return []
        # Coinbase row: [time_seconds, low, high, open, close, volume]
        # WEEX shape:   [open_time_ms, open, high, low, close, volume]
        converted = []
        for row in rows:
            try:
                ts_s = int(row[0])
                converted.append([
                    ts_s * 1000,        # open_time_ms
                    str(row[3]),         # open
                    str(row[2]),         # high
                    str(row[1]),         # low
                    str(row[4]),         # close
                    str(row[5]),         # volume
                ])
            except (IndexError, TypeError, ValueError):
                continue
        # Coinbase returns newest-first; reverse for chronological-within-chunk
        converted.reverse()
        return converted
    except (requests.RequestException, ValueError) as e:
        logger.warning("Coinbase klines request failed for %s %s: %s",
                          symbol, interval, e)
        return []


def fetch_klines_chained(symbol: str, interval: str, total_bars: int) -> list:
    """Paginate backward in time to accumulate `total_bars` of historical
    klines from Coinbase. Returns rows in CHRONOLOGICAL order (oldest
    first) to match the WEEX shape signals.build_dataframe expects.

    Implementation: walks BACKWARD from now in 300-bar chunks. Each
    subsequent call's `end` parameter = previous call's earliest
    open_time - 1ms.
    """
    if interval.lower() not in _INTERVAL_GRANULARITY:
        raise ValueError(f"Unsupported interval: {interval}")
    if total_bars <= 0:
        return []

    accumulated: list = []
    end_time_ms: Optional[int] = None
    remaining = total_bars

    while remaining > 0:
        chunk_limit = min(_PER_CALL_LIMIT, remaining)
        chunk = _one_call(symbol, interval, end_time_ms, chunk_limit)
        if not chunk:
            break
        accumulated = chunk + accumulated
        oldest_open_time = int(chunk[0][0])
        end_time_ms = oldest_open_time - 1
        remaining -= len(chunk)
        if len(chunk) < chunk_limit:
            break
        time.sleep(_RATE_LIMIT_SLEEP_S)

    if len(accumulated) > total_bars:
        accumulated = accumulated[-total_bars:]

    return accumulated
