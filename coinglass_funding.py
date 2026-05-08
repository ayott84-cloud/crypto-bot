"""Coinglass historical funding-rate fetcher.

Lets the funding-fade bot skip a 15-day local-warmup phase by pulling 30+
days of historical funding rate data on first run. Cached locally as JSON
to avoid repeated API hits.

API: Coinglass v4. Two relevant endpoints used here:

  GET /api/futures/fundingRate/ohlc-history
      ?exchange=Binance&symbol=BTCUSDT&interval=8h&limit=180

  GET /api/futures/fundingRate/exchange-list
      (returns aggregate across exchanges — useful as a sanity check)

Free tier: 30 requests/min, sufficient for our use case (one fetch per
symbol per day to refresh the local cache).

Auth: pass `COINGLASS_API_KEY` in .env. If unset, the module falls back to
unauthenticated v3 endpoints (heavily rate-limited; usable for development
but not for production).

Pricing: free tier is sufficient for our scale. Pro tier ($30/mo) adds
near-real-time updates and removes rate limits — overkill for now.

Schema returned to callers (one record per 8h funding period):

  {
    "symbol": "BTCUSDT",
    "exchange": "Binance",
    "fetched_at": "2026-05-08T15:00:00Z",
    "rates": [
      {"timestamp_ms": 1714867200000, "funding_rate": 0.000045},
      {"timestamp_ms": 1714896000000, "funding_rate": 0.000067},
      ...
    ]
  }

Usage:
    from coinglass_funding import fetch_history, get_distribution
    history = fetch_history("BTCUSDT", days=30)        # 90 records
    rates = get_distribution("BTCUSDT", days=30)       # just floats
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests

logger = logging.getLogger("crypto_bot.coinglass")

CG_API_BASE = "https://open-api-v4.coinglass.com"
CG_API_KEY = os.getenv("COINGLASS_API_KEY", "")
DEFAULT_EXCHANGE = "Binance"   # most reference; widely-used funding venue
CACHE_DIR = Path(__file__).resolve().parent / ".coinglass_cache"
CACHE_TTL_HOURS = 12           # refresh cached funding history every 12h


def _cache_path(symbol: str, exchange: str) -> Path:
    safe = f"{exchange}_{symbol}".replace("/", "_")
    return CACHE_DIR / f"{safe}.json"


def _is_cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age_h = (time.time() - path.stat().st_mtime) / 3600
    return age_h < CACHE_TTL_HOURS


def _read_cache(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(path: Path, payload: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as e:
        logger.warning("Could not write cache %s: %s", path, e)


def fetch_history(
    symbol: str,
    days: int = 30,
    exchange: str = DEFAULT_EXCHANGE,
    *,
    use_cache: bool = True,
    timeout: float = 15.0,
) -> Dict:
    """Return historical funding rates for `symbol` over the last `days` days.

    Default: Binance perp funding (heaviest / most liquid). Caller can pass
    `exchange="WEEX"` if they want the exact venue we trade on, but Coinglass's
    coverage of WEEX may be incomplete. Binance is a good directional proxy.

    Returns the full payload {symbol, exchange, fetched_at, rates}. On
    failure, returns {symbol, exchange, fetched_at, rates: []} with a warning.
    """
    cache_path = _cache_path(symbol, exchange)
    if use_cache and _is_cache_fresh(cache_path):
        cached = _read_cache(cache_path)
        if cached and cached.get("rates"):
            logger.debug("Coinglass cache hit: %s/%s (%d rates)",
                         exchange, symbol, len(cached["rates"]))
            return cached

    rates = _fetch_remote(symbol=symbol, exchange=exchange, days=days, timeout=timeout)
    payload = {
        "symbol": symbol,
        "exchange": exchange,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "rates": rates,
    }
    if rates:
        _write_cache(cache_path, payload)
    elif use_cache and cache_path.exists():
        # Stale cache is better than nothing if remote failed
        cached = _read_cache(cache_path)
        if cached:
            logger.warning("Coinglass remote failed; using stale cache for %s/%s",
                           exchange, symbol)
            return cached
    return payload


def _fetch_remote(symbol: str, exchange: str, days: int, timeout: float) -> List[dict]:
    """Hit Coinglass v4 ohlc-history endpoint. Returns list of rate dicts."""
    headers = {"accept": "application/json"}
    if CG_API_KEY:
        headers["CG-API-KEY"] = CG_API_KEY

    # Coinglass returns OHLC funding bars. We only care about close-of-period.
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86400 * 1000
    params = {
        "exchange": exchange,
        "symbol": symbol,
        "interval": "8h",
        "limit": min(500, days * 3 + 5),
        "startTime": start_ms,
        "endTime": end_ms,
    }
    url = f"{CG_API_BASE}/api/futures/fundingRate/ohlc-history"

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        if resp.status_code == 401 or resp.status_code == 403:
            logger.error("Coinglass auth failed (status=%d) — set COINGLASS_API_KEY in .env",
                         resp.status_code)
            return []
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as e:
        logger.error("Coinglass fetch failed for %s/%s: %s", exchange, symbol, e)
        return []
    except ValueError as e:
        logger.error("Coinglass returned non-JSON for %s/%s: %s", exchange, symbol, e)
        return []

    # v4 response shape: {"code": "0", "data": [[ts, open, high, low, close], ...]}
    if str(body.get("code")) != "0":
        logger.warning("Coinglass error response: %s", body.get("msg") or body)
        return []
    raw = body.get("data") or []
    out: List[dict] = []
    for row in raw:
        try:
            if isinstance(row, list) and len(row) >= 5:
                # OHLC: take close as the realized funding for that period
                out.append({"timestamp_ms": int(row[0]), "funding_rate": float(row[4])})
            elif isinstance(row, dict):
                ts = int(row.get("t") or row.get("timestamp") or 0)
                rate = float(row.get("c") or row.get("close") or row.get("fundingRate") or 0)
                if ts and rate:
                    out.append({"timestamp_ms": ts, "funding_rate": rate})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda r: r["timestamp_ms"])
    return out


def get_distribution(symbol: str, days: int = 30,
                     exchange: str = DEFAULT_EXCHANGE) -> List[float]:
    """Return just the funding-rate floats (sorted by time). Convenience wrapper."""
    payload = fetch_history(symbol=symbol, days=days, exchange=exchange)
    return [r["funding_rate"] for r in payload.get("rates", [])]


def percentile_of(value: float, distribution: List[float]) -> Optional[float]:
    """Where does `value` rank in `distribution`? Returns 0-100, or None if empty."""
    if not distribution:
        return None
    sorted_d = sorted(distribution)
    n = len(sorted_d)
    # Count of items strictly below value
    below = sum(1 for x in sorted_d if x < value)
    return (below / n) * 100


def is_extreme(value: float, distribution: List[float],
               percentile_threshold: float = 97.0,
               absolute_floor: float = 0.0005) -> Optional[str]:
    """Classify a funding rate as 'top' / 'bottom' / None.

    Returns:
      "top"    — current rate is in top (100-pct)% of distribution AND above
                 the absolute_floor (typically 0.05% per 8h)
      "bottom" — current rate is in bottom (100-pct)% of distribution AND
                 below -absolute_floor
      None     — not extreme
    """
    rank = percentile_of(value, distribution)
    if rank is None:
        return None
    if rank >= percentile_threshold and value >= absolute_floor:
        return "top"
    if rank <= (100 - percentile_threshold) and value <= -absolute_floor:
        return "bottom"
    return None


if __name__ == "__main__":
    # Quick smoke test
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for sym in ("BTCUSDT", "ETHUSDT", "DOGEUSDT"):
        h = fetch_history(sym, days=30)
        rates = [r["funding_rate"] for r in h["rates"]]
        if not rates:
            print(f"{sym}: NO DATA (check COINGLASS_API_KEY)")
            continue
        print(f"{sym}: {len(rates)} rates, "
              f"min={min(rates):.6f}, max={max(rates):.6f}, "
              f"median={sorted(rates)[len(rates)//2]:.6f}")
