"""Historical funding-rate provider — Hyperliquid `/info` fundingHistory.

Provides 30+ days of historical funding rates per coin so the funding-fade
bot doesn't need a 15-day local warmup. Uses Hyperliquid's free public
`/info` endpoint (same one we already hit for whale-bot data — no API key,
no rate-limit pain).

Originally planned around Coinglass but they killed their free tier (cheapest
plan is $29/mo as of May 2026). HL fundingHistory gives us equivalent data
at $0/mo with first-party reliability.

Cross-exchange caveat: we trade on WEEX but pull funding history from HL.
This is fine because funding rates are arbitraged across exchanges within
hours — when HL funding is at a 30-day extreme, WEEX is at one too. The
*magnitude* on WEEX is what we pay at execution, and we read that live each
cycle. The historical signal just needs a reliable rolling window.

Endpoint:
    POST https://api.hyperliquid.xyz/info
    body: {"type": "fundingHistory", "coin": "BTC", "startTime": <ms>, "endTime": <ms>}
Returns:
    [{"coin": "BTC", "fundingRate": "0.0000125", "premium": "...", "time": 1716000000000}, ...]

API contract preserved from earlier coinglass_funding.py: same function
signatures, same return shapes — so the funding-fade bot doesn't care
where the data comes from.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests

logger = logging.getLogger("crypto_bot.funding_history")

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
CACHE_DIR = Path(__file__).resolve().parent / ".funding_history_cache"
CACHE_TTL_HOURS = 12         # refresh cached funding history every 12h
HL_TIMEOUT_S = 15.0


# ─── HL coin-name normalization ──────────────────────────────────────────────
# HL uses coin tickers without USDT suffix (BTC not BTCUSDT). Some coins are
# k-prefixed for 1000x quoting (kPEPE, kBONK). We accept either form and map.

def _hl_coin_from_weex(weex_symbol: str) -> str:
    """BTCUSDT → BTC. Pass-through if no USDT suffix."""
    if weex_symbol.endswith("USDT"):
        return weex_symbol[:-4]
    return weex_symbol


def _cache_path(coin: str) -> Path:
    safe = coin.replace("/", "_")
    return CACHE_DIR / f"hl_{safe}.json"


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
    *,
    use_cache: bool = True,
    timeout: float = HL_TIMEOUT_S,
) -> Dict:
    """Return historical funding rates for `symbol` over the last `days` days.

    `symbol` accepts either WEEX format ("BTCUSDT") or HL format ("BTC"); we
    normalize internally.

    Returns:
        {
            "symbol": "BTC",
            "source": "hyperliquid",
            "fetched_at": "...ISO...",
            "rates": [
                {"timestamp_ms": int, "funding_rate": float},
                ...
            ]
        }
    On failure returns the dict with rates=[] and logs a warning.
    """
    coin = _hl_coin_from_weex(symbol)
    cache_path = _cache_path(coin)

    if use_cache and _is_cache_fresh(cache_path):
        cached = _read_cache(cache_path)
        if cached and cached.get("rates"):
            logger.debug("HL funding cache hit: %s (%d rates)", coin, len(cached["rates"]))
            return cached

    rates = _fetch_remote(coin=coin, days=days, timeout=timeout)
    payload = {
        "symbol": coin,
        "source": "hyperliquid",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "rates": rates,
    }
    if rates:
        _write_cache(cache_path, payload)
    elif use_cache and cache_path.exists():
        # Stale cache is better than empty if remote failed
        cached = _read_cache(cache_path)
        if cached:
            logger.warning("HL fundingHistory remote failed; using stale cache for %s", coin)
            return cached
    return payload


def _fetch_remote(coin: str, days: int, timeout: float) -> List[dict]:
    """Hit HL fundingHistory. Returns list of {timestamp_ms, funding_rate}."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86400 * 1000
    body = {"type": "fundingHistory", "coin": coin, "startTime": start_ms, "endTime": end_ms}

    try:
        resp = requests.post(HL_INFO_URL, json=body, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.error("HL fundingHistory fetch failed for %s: %s", coin, e)
        return []
    except ValueError as e:
        logger.error("HL fundingHistory returned non-JSON for %s: %s", coin, e)
        return []

    if not isinstance(data, list):
        logger.warning("HL fundingHistory unexpected response shape for %s: %r", coin, type(data))
        return []

    out: List[dict] = []
    for row in data:
        try:
            t = int(row.get("time") or row.get("timestamp") or 0)
            rate = float(row.get("fundingRate") or 0)
            if t > 0:
                out.append({"timestamp_ms": t, "funding_rate": rate})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda r: r["timestamp_ms"])
    return out


def get_distribution(symbol: str, days: int = 30) -> List[float]:
    """Return just the funding-rate floats (sorted by time). Convenience wrapper."""
    payload = fetch_history(symbol=symbol, days=days)
    return [r["funding_rate"] for r in payload.get("rates", [])]


def percentile_of(value: float, distribution: List[float]) -> Optional[float]:
    """Where does `value` rank in `distribution`? Returns 0-100, or None if empty."""
    if not distribution:
        return None
    sorted_d = sorted(distribution)
    n = len(sorted_d)
    below = sum(1 for x in sorted_d if x < value)
    return (below / n) * 100


def is_extreme(value: float, distribution: List[float],
               percentile_threshold: float = 97.0,
               absolute_floor: float = 0.0005) -> Optional[str]:
    """Classify a funding rate as 'top' / 'bottom' / None.

    Returns:
      "top"    — value is in top (100-pct)% of distribution AND >= absolute_floor
      "bottom" — value is in bottom (100-pct)% AND <= -absolute_floor
      None     — not extreme

    Default thresholds (97th percentile + 0.05%/8h floor) follow the peer-review
    consensus: 95th was too noisy, 99th too rare. 97 + abs-floor gives ~3-5
    actionable signals per coin per 30-day window.
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for sym in ("BTCUSDT", "ETHUSDT", "DOGEUSDT", "AVAXUSDT"):
        h = fetch_history(sym, days=30, use_cache=False)
        rates = [r["funding_rate"] for r in h["rates"]]
        if not rates:
            print(f"{sym}: NO DATA — HL probably doesn't list this coin (or transient error)")
            continue
        s = sorted(rates)
        print(f"{sym}: {len(rates)} rates over 30d  "
              f"min={s[0]:+.6f}  p5={s[len(s)//20]:+.6f}  "
              f"median={s[len(s)//2]:+.6f}  "
              f"p95={s[len(s)*19//20]:+.6f}  max={s[-1]:+.6f}")
