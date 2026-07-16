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

# Transient-transport retry policy (Jul 16 2026): sustained chunk chains
# provoke occasional SSLEOFError connection drops from Coinbase. Before
# this, ONE dropped request ended the whole chain as if history were
# exhausted — the momentum re-window and trailing A/B ran on silently
# truncated windows (SOL's chain died at Feb 2025, mid-history).
_RETRY_SLEEPS = (2.0, 5.0)          # attempt + 2 retries
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class TransientFetchError(Exception):
    """A transport-level failure that persisted through all retries.
    fetch_klines_chained catches it to stop the chain LOUDLY."""


def _get_with_retries(url: str, params: dict, symbol: str, interval: str):
    """requests.get with backoff on transport errors + retryable HTTP
    statuses. Returns the Response (any status outside the retryable
    set, including 200 and 404). Raises TransientFetchError when every
    attempt failed transiently."""
    last_err = None
    for i, sleep_s in enumerate((0.0,) + _RETRY_SLEEPS):
        if sleep_s:
            logger.warning("Coinbase retry %d/%d for %s %s in %.0fs: %s",
                             i, len(_RETRY_SLEEPS), symbol, interval,
                             sleep_s, last_err)
            time.sleep(sleep_s)
        try:
            r = requests.get(url, params=params, timeout=15.0,
                              headers={"User-Agent": "crypto-bot-backtest/1.0"})
        except requests.RequestException as e:
            last_err = e
            continue
        if r.status_code in _RETRYABLE_STATUS:
            last_err = f"HTTP {r.status_code}"
            continue
        return r
    raise TransientFetchError(
        f"{symbol} {interval}: all retries exhausted ({last_err})")

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
    # Breakout long-window universe (P4 Step 1)
    "NEARUSDT": "NEAR-USD",
    "AAVEUSDT": "AAVE-USD",
    "INJUSDT":  "INJ-USD",
    # Momentum long-window universe (Jul 4 run showed 0 trades for these
    # — missing mappings, not missing signals). All Coinbase-listed.
    "DOTUSDT":    "DOT-USD",
    "LTCUSDT":    "LTC-USD",
    "UNIUSDT":    "UNI-USD",
    "FILUSDT":    "FIL-USD",
    "ETCUSDT":    "ETC-USD",
    "APTUSDT":    "APT-USD",
    "ARBUSDT":    "ARB-USD",
    "ATOMUSDT":   "ATOM-USD",
    "SUIUSDT":    "SUI-USD",
    "HBARUSDT":   "HBAR-USD",
    "OPUSDT":     "OP-USD",
    "RENDERUSDT": "RENDER-USD",
    "SHIBUSDT":   "SHIB-USD",
    "ICPUSDT":    "ICP-USD",
}

# Intervals Coinbase lacks natively, synthesized by aggregating a finer
# granularity: target → (base_interval, factor).
_AGGREGATE_BASE = {
    "4h":  ("1h", 4),
    "2h":  ("1h", 2),
    "30m": ("15m", 2),
    "3m":  ("1m", 3),
}


def _aggregate_rows(rows: list, target_ms: int) -> list:
    """Aggregate chronological finer-granularity rows into target_ms
    buckets (open=first, high=max, low=min, close=last, volume=sum).

    A partial LEADING bucket (pagination cut mid-bucket) is dropped; the
    trailing bucket is kept — replays already treat the last bar as
    forming (iloc[-2] convention)."""
    if not rows:
        return []
    buckets: dict = {}
    order: list = []
    for r in rows:
        open_ms = int(r[0])
        b = open_ms // target_ms
        if b not in buckets:
            buckets[b] = {
                "first_ts": open_ms,
                "open": r[1], "high": float(r[2]), "low": float(r[3]),
                "close": r[4], "volume": float(r[5]),
            }
            order.append(b)
        else:
            agg = buckets[b]
            agg["high"] = max(agg["high"], float(r[2]))
            agg["low"] = min(agg["low"], float(r[3]))
            agg["close"] = r[4]
            agg["volume"] += float(r[5])
    # Drop a partial leading bucket: its first constituent bar doesn't
    # sit on the bucket boundary.
    if order and buckets[order[0]]["first_ts"] % target_ms != 0:
        order = order[1:]
    out = []
    for b in order:
        agg = buckets[b]
        open_time_ms = b * target_ms
        out.append([
            open_time_ms,
            str(agg["open"]), str(agg["high"]), str(agg["low"]),
            str(agg["close"]), str(agg["volume"]),
            open_time_ms + target_ms - 1,
            "0", "0", "0", "0",
        ])
    return out


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
        r = _get_with_retries(
            _COINBASE_BASE + _KLINES_PATH.format(product_id=product_id),
            params, symbol, interval)
        if r.status_code != 200:
            # Non-retryable status (e.g. 404 unknown product) — a data
            # answer, not a transport failure. No retry storm.
            logger.warning("Coinbase klines HTTP %d for %s %s: %s",
                              r.status_code, symbol, interval, r.text[:200])
            return []
        rows = r.json() or []
        if not isinstance(rows, list):
            logger.warning("Coinbase unexpected payload for %s %s: %s",
                              symbol, interval, str(rows)[:200])
            return []
        # Coinbase row: [time_seconds, low, high, open, close, volume]
        # WEEX shape:   [open_time_ms, open, high, low, close, volume,
        #                close_time_ms, quote_vol, num_trades, tbb, tbq]
        # build_dataframe() in signals.py unconditionally reads
        # df["close_time"], so we MUST emit the 11-column shape even
        # when most fields are placeholders. Without this, replay_*
        # crashes with KeyError: 'close_time'.
        interval_ms = _interval_ms(interval)
        converted = []
        for row in rows:
            try:
                ts_s = int(row[0])
                open_time_ms = ts_s * 1000
                close_time_ms = open_time_ms + interval_ms - 1
                converted.append([
                    open_time_ms,         # open_time_ms
                    str(row[3]),           # open
                    str(row[2]),           # high
                    str(row[1]),           # low
                    str(row[4]),           # close
                    str(row[5]),           # volume
                    close_time_ms,         # close_time_ms
                    "0",                   # quote_volume (placeholder)
                    "0",                   # num_trades (placeholder)
                    "0",                   # taker_buy_volume (placeholder)
                    "0",                   # taker_buy_quote_volume (placeholder)
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
    iv = interval.lower()
    if iv in _AGGREGATE_BASE:
        # Coinbase lacks this granularity natively — fetch the finer base
        # and aggregate. +2 extra buckets of base bars cover the dropped
        # partial leading bucket.
        base_iv, factor = _AGGREGATE_BASE[iv]
        base_rows = fetch_klines_chained(symbol, base_iv,
                                           (total_bars + 2) * factor)
        rows = _aggregate_rows(base_rows, _interval_ms(iv))
        return rows[-total_bars:] if len(rows) > total_bars else rows
    if iv not in _INTERVAL_GRANULARITY:
        raise ValueError(f"Unsupported interval: {interval}")
    if total_bars <= 0:
        return []

    accumulated: list = []
    end_time_ms: Optional[int] = None
    remaining = total_bars

    while remaining > 0:
        chunk_limit = min(_PER_CALL_LIMIT, remaining)
        try:
            chunk = _one_call(symbol, interval, end_time_ms, chunk_limit)
        except TransientFetchError as e:
            oldest = (datetime.fromtimestamp(int(accumulated[0][0]) / 1000,
                                                tz=timezone.utc).isoformat()
                       if accumulated else "n/a")
            logger.warning(
                "%s %s chain TRUNCATED: %d of %d bars fetched (oldest %s) — %s",
                symbol, interval, len(accumulated), total_bars, oldest, e)
            break
        if not chunk:
            break   # empty chunk = history genuinely exhausted
        oldest_open_time = int(chunk[0][0])
        # No-progress guard BEFORE accumulating: a payload that doesn't
        # reach further back (static mock, API echo) would otherwise be
        # appended as a duplicate and loop forever.
        if end_time_ms is not None and oldest_open_time - 1 >= end_time_ms:
            break
        accumulated = chunk + accumulated
        end_time_ms = oldest_open_time - 1
        remaining -= len(chunk)
        # NOTE: do NOT break on short chunks. Coinbase routinely returns
        # 299 rows for a 300-bar window (boundary rounding, sparse
        # candles); the old `len(chunk) < chunk_limit` break silently
        # truncated a 4500-bar request to ~375 bars. Only an EMPTY chunk
        # means there is nothing further back.
        time.sleep(_RATE_LIMIT_SLEEP_S)

    if len(accumulated) > total_bars:
        accumulated = accumulated[-total_bars:]

    return accumulated
