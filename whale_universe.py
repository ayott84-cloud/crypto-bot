"""Top-N market-cap universe for the whale bot.

Fetches the top coins by market cap from CoinGecko's free public endpoint and
caches the list to disk for 24 hours. Used by whale_signals.hl_coin_to_weex_symbol()
to reject signals on coins outside the top N — filters out the illiquid long tail
while still letting the bot react to whatever whales are trading at size.

CoinGecko endpoint: /api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=100
- Free, no API key, rate-limited ~30 req/min (we fetch once per 24h).

Symbol mapping nuance:
- CoinGecko symbols are lowercase ("btc", "eth", "xrp", "kpepe" nonexistent).
- Hyperliquid uses "BTC", "ETH", "kPEPE" (k-prefix for 1000x-quoted coins).
- WEEX uses "BTCUSDT", "ETHUSDT", "PEPEUSDT" (plain).
- We store the CoinGecko symbol UPPERCASED to match HL convention, AND strip the
  leading "k" for lookup (so whales holding kPEPE match PEPE in the top-100).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Set

import requests

from whale_config import (
    WHALE_UNIVERSE_CG_URL,
    WHALE_TOP100_CACHE,
    WHALE_TOP100_TTL_HOURS,
    WHALE_MARKETCAP_RANK_LIMIT,
)

logger = logging.getLogger("crypto_bot.whale_universe")

# Fallback list — used only if CoinGecko is unreachable on startup. Top ~30 by
# market cap as of April 2026. Keeps the bot running in offline/degraded mode.
_FALLBACK_TOP_SYMBOLS = {
    "BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "TRX", "AVAX", "LINK", "BNB",
    "DOT", "LTC", "MATIC", "NEAR", "UNI", "FIL", "ETC", "APT", "ARB", "ATOM",
    "SUI", "HBAR", "AAVE", "OP", "INJ", "RENDER", "SHIB", "PEPE", "TON", "ICP",
}


def _normalize_coin(coin: str) -> str:
    """Normalize HL coin name for top-100 lookup.

    kPEPE → PEPE (strip k-prefix, which is HL's 1000x quote notation)
    btc → BTC (uppercase)
    """
    c = coin.strip().upper()
    if c.startswith("K") and len(c) > 1 and c[1].isalpha():
        # Only strip 'K' if it's HL's k-prefix (1000x multiplier). Real coins
        # whose tickers legitimately start with 'K' are rare; hard-coded overrides
        # go in whale_config.HL_TO_WEEX_SYMBOL_OVERRIDES if needed.
        stripped = c[1:]
        # Check against whichever top set is currently loaded: test stub
        # (_top_symbols), disk cache, or fallback.
        known_top = _top_symbols if _top_symbols is not None else (
            _FALLBACK_TOP_SYMBOLS | _loaded_top_cache()
        )
        if stripped in known_top:
            return stripped
    return c


def refresh_and_cache_top100(force: bool = False) -> Set[str]:
    """Fetch top-N coins from CoinGecko and cache. Returns the set of uppercased
    symbols.

    force=True skips the cache-freshness check.
    """
    cache: Path = WHALE_TOP100_CACHE

    if not force and cache.exists():
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(payload["fetched_at"])
            age_h = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600
            if age_h < WHALE_TOP100_TTL_HOURS:
                logger.info("Using cached top-%d (age %.1fh)",
                            WHALE_MARKETCAP_RANK_LIMIT, age_h)
                return set(payload["symbols"])
        except (ValueError, KeyError, OSError) as e:
            logger.warning("Top-100 cache unreadable, refetching: %s", e)

    # Live fetch
    per_page = min(250, WHALE_MARKETCAP_RANK_LIMIT)
    pages_needed = (WHALE_MARKETCAP_RANK_LIMIT + per_page - 1) // per_page
    symbols: Set[str] = set()

    try:
        for page in range(1, pages_needed + 1):
            resp = requests.get(WHALE_UNIVERSE_CG_URL, params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": per_page,
                "page": page,
                "sparkline": "false",
            }, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            for row in data:
                sym = row.get("symbol", "").strip().upper()
                if sym:
                    symbols.add(sym)
            if len(data) < per_page:
                break
        if len(symbols) < 10:
            raise RuntimeError(f"CoinGecko returned too few symbols ({len(symbols)})")
        cache.write_text(json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "rank_limit": WHALE_MARKETCAP_RANK_LIMIT,
            "symbols": sorted(symbols),
        }, indent=2), encoding="utf-8")
        logger.info("Refreshed top-%d from CoinGecko: %d symbols cached",
                    WHALE_MARKETCAP_RANK_LIMIT, len(symbols))
        return symbols
    except Exception as e:
        logger.error("CoinGecko fetch failed: %s — using fallback list", e)
        # Try stale cache first, then fallback
        if cache.exists():
            try:
                payload = json.loads(cache.read_text(encoding="utf-8"))
                stale = set(payload.get("symbols", []))
                if stale:
                    logger.warning("Using stale top-100 cache (%d symbols)", len(stale))
                    return stale
            except (ValueError, OSError):
                pass
        return set(_FALLBACK_TOP_SYMBOLS)


def _loaded_top_cache() -> Set[str]:
    """Non-refreshing read of whatever's currently in the cache. Internal use."""
    cache: Path = WHALE_TOP100_CACHE
    if not cache.exists():
        return set()
    try:
        payload = json.loads(cache.read_text(encoding="utf-8"))
        return set(payload.get("symbols", []))
    except (ValueError, OSError):
        return set()


# Module-level lazily-loaded set. First call triggers fetch; subsequent calls reuse
# until the cache TTL expires (then next call refreshes).
_top_symbols: Optional[Set[str]] = None


def get_top_symbols(force_refresh: bool = False) -> Set[str]:
    """Return the current top-N symbol set (uppercase, plain like 'BTC')."""
    global _top_symbols
    if force_refresh or _top_symbols is None:
        _top_symbols = refresh_and_cache_top100(force=force_refresh)
    return _top_symbols


def is_top100(coin: str) -> bool:
    """True iff `coin` (any case, with or without 'k' prefix) is in the cached top-N."""
    if not coin:
        return False
    top = get_top_symbols()
    normalized = _normalize_coin(coin)
    return normalized in top


def set_top_symbols_for_test(symbols: Set[str]) -> None:
    """Test helper: inject a fixed top-N set, bypassing network and cache."""
    global _top_symbols
    _top_symbols = set(symbols)
