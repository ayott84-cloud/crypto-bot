"""Phase W.E.2 — Arkham on-chain flow data adapter.

Pulls 24h net flow (USD) for a coin from Arkham's /token/top_flow/{chain}
endpoint. Used by check_arkham_flow_gate in whale_filters.py to turn the
(lagging) HL leaderboard signal into a leading-confirmation flow:

    Before opening whale LONG on coin X, check Arkham for 24h net flow.
    If top entities are net DISTRIBUTORS → skip (the trade is already in
    distribution). Mirror for SHORT.

Graceful degradation everywhere — missing API key, unknown coin, HTTP
failure, malformed response → return None. The filter treats None as
"no signal, pass". The gate is purely additive.

Requires: ARKHAM_API_KEY env var.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Optional

logger = logging.getLogger("crypto_bot.whale_arkham")


_BASE_URL = "https://api.arkm.com"
_REQUEST_TIMEOUT_S = 5.0

# Map HL coin → Arkham chain slug for /token/top_flow/{chain}. Whale bot's
# HL universe is largely ETH-resident tokens; BTC + SOL have their own
# native chains. Extend this map as the operator validates each coin's
# response shape and signal quality.
_COIN_TO_CHAIN: dict[str, str] = {
    "BTC":  "bitcoin",
    "ETH":  "ethereum",
    "SOL":  "solana",
    "USDC": "ethereum",
    "USDT": "ethereum",
    "LINK": "ethereum",
    "AAVE": "ethereum",
    "ARB":  "arbitrum",
    "PEPE": "ethereum",
}


def _get_api_key() -> Optional[str]:
    key = os.getenv("ARKHAM_API_KEY", "").strip()
    return key or None


def _extract_net_flow(row: dict) -> Optional[float]:
    """Pull a numeric net flow value from one Arkham response row. Returns
    None if no recognized field is present or values aren't numeric.

    Accepts several common field names — Arkham response shape varies by
    endpoint family. Falls back to inflow - outflow when no explicit
    net field is present.
    """
    for field in ("net_flow", "netFlow", "delta", "net"):
        v = row.get(field)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    inflow_raw = row.get("inflow") or row.get("inflow_usd") or row.get("inflowUsd")
    outflow_raw = row.get("outflow") or row.get("outflow_usd") or row.get("outflowUsd")
    if inflow_raw is None and outflow_raw is None:
        return None
    try:
        return float(inflow_raw or 0) - float(outflow_raw or 0)
    except (TypeError, ValueError):
        return None


def fetch_token_net_flow_24h(coin: str,
                                timeout: float = _REQUEST_TIMEOUT_S) -> Optional[float]:
    """Fetch the last-24h net entity flow (USD) for `coin` from Arkham.

    Returns:
      float — positive = net accumulation by top entities (bullish)
              negative = net distribution                  (bearish)
      None  — missing API key, unmapped coin, HTTP failure, parse failure,
              or coin not in the response. The caller (filter) treats
              None as 'no signal → pass'.
    """
    key = _get_api_key()
    if not key:
        return None

    chain = _COIN_TO_CHAIN.get(coin.upper())
    if not chain:
        logger.debug("[%s] no Arkham chain mapping — gate degrades to pass", coin)
        return None

    url = f"{_BASE_URL}/token/top_flow/{chain}?timeLast=24h"
    try:
        req = urllib.request.Request(url, headers={"API-Key": key})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            data = json.loads(raw.decode("utf-8"))
    except Exception as e:  # noqa: BLE001 — covers urllib + json + socket failures
        logger.debug("[%s] Arkham top_flow fetch failed: %s", coin, e)
        return None

    # Accept top-level list OR envelope shapes {data: [...]} / {rows: [...]}
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("data") or data.get("rows") or []
    else:
        return None
    if not isinstance(rows, list):
        return None

    coin_u = coin.upper()
    for row in rows:
        if not isinstance(row, dict):
            continue
        sym = (row.get("symbol") or row.get("token") or row.get("ticker") or "").upper()
        if sym != coin_u:
            continue
        return _extract_net_flow(row)

    return None
