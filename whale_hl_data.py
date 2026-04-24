"""Hyperliquid market data fetcher — funding rates, OI, mark prices.

Fetches once per cycle via /info type="metaAndAssetCtxs" (one HTTP call,
returns every coin at once). Used by classify() for funding + OI confluence.

Response schema (trimmed):
    [
      {"universe": [{"name": "BTC", ...}, {"name": "ETH", ...}, ...]},
      [
        {"funding": "0.0000125", "openInterest": "83400.5", "markPx": "78012.3", "premium": "...", ...},
        {"funding": "...", "openInterest": "...", "markPx": "...", ...},
        ...
      ]
    ]
The two lists align index-wise.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional

import requests

logger = logging.getLogger("crypto_bot.whale_hl_data")

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
_HL_TIMEOUT_S = 10.0
_HL_RETRIES = 3
_HL_BACKOFF = [1, 3, 8]


@dataclass
class HLContext:
    """Per-coin live market context from Hyperliquid."""
    coin: str
    funding_rate: float        # per-8h rate (e.g. 0.0001 = 0.01% per 8h)
    open_interest: float       # in coin units
    mark_price: float
    premium: float             # perp - spot oracle
    oi_usd: float              # OI in USD (open_interest * mark_price)

    @property
    def funding_annualized_pct(self) -> float:
        # 3 funding periods per day × 365
        return self.funding_rate * 3 * 365 * 100


def fetch_meta_and_ctxs() -> Dict[str, HLContext]:
    """Fetch live market context for every HL perp. Returns {COIN -> HLContext}.

    Single HTTP call. Retries on transient failure. Returns {} on total failure
    (caller should degrade gracefully — confluence filters skip without it).
    """
    body = {"type": "metaAndAssetCtxs"}
    last_err: Optional[Exception] = None
    for attempt, backoff in enumerate([0] + _HL_BACKOFF):
        if backoff:
            time.sleep(backoff)
        try:
            resp = requests.post(HL_INFO_URL, json=body, timeout=_HL_TIMEOUT_S)
            resp.raise_for_status()
            data = resp.json()
            if not (isinstance(data, list) and len(data) >= 2):
                raise ValueError(f"Unexpected HL response shape: {type(data)}, len={len(data) if isinstance(data, list) else '?'}")
            universe = data[0].get("universe", [])
            ctxs = data[1]
            if not isinstance(ctxs, list) or len(ctxs) != len(universe):
                raise ValueError(f"Universe/ctxs length mismatch: {len(universe)} vs {len(ctxs) if isinstance(ctxs, list) else '?'}")
            out: Dict[str, HLContext] = {}
            for i, u in enumerate(universe):
                name = u.get("name", "")
                c = ctxs[i]
                try:
                    funding = float(c.get("funding", 0))
                    oi = float(c.get("openInterest", 0))
                    mark = float(c.get("markPx", 0))
                    premium = float(c.get("premium", 0))
                    out[name] = HLContext(
                        coin=name,
                        funding_rate=funding,
                        open_interest=oi,
                        mark_price=mark,
                        premium=premium,
                        oi_usd=oi * mark,
                    )
                except (ValueError, TypeError):
                    continue  # skip malformed rows
            logger.info("Fetched HL market context for %d coins", len(out))
            return out
        except Exception as e:
            last_err = e
            logger.warning("metaAndAssetCtxs attempt %d failed: %s", attempt + 1, e)
    logger.error("metaAndAssetCtxs failed after %d attempts: %s", _HL_RETRIES + 1, last_err)
    return {}
