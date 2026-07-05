"""R7 — Polymarket + Kalshi READ-ONLY cross-venue spread scanner.

Phase O decision (Jul 2026): research-first. This tool contains NO
order-placement code and uses public, unauthenticated endpoints only.
It accumulates spread observations to prediction_spreads.jsonl so at
least a week of evidence exists before ANY execution discussion — the
course's own caveat applies: fees + slippage typically eat cross-venue
gaps under ~5 cents, so a flagged gap is a research lead, not a trade.

Run (droplet): venv/bin/python tools/prediction_scanner.py [--top N] [--quiet]
Installed by: deploy/crypto-prediction-scan.service + .timer (daily)
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

_POLY_URL = ("https://gamma-api.polymarket.com/markets"
              "?active=true&closed=false&limit=200"
              "&order=volume24hr&ascending=false")
_KALSHI_URL = ("https://api.elections.kalshi.com/trade-api/v2/markets"
                "?status=open&limit=200")
_LOG_PATH = BOT_DIR / "prediction_spreads.jsonl"

MATCH_RATIO = 0.75
GAP_FLOOR = 0.03          # |gap| below this is noise
FEE_CAVEAT = ("CAVEAT: fees + slippage typically consume cross-venue gaps "
               "under ~5c — flags are research leads, NOT trades.")


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (title or "").lower()).strip()


def normalize_polymarket(raw: dict):
    """Gamma market row → common shape, or None when malformed."""
    try:
        prices = raw.get("outcomePrices")
        if isinstance(prices, str):
            prices = json.loads(prices)
        outcomes = raw.get("outcomes")
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        yes_idx = 0
        if outcomes and "Yes" in outcomes:
            yes_idx = outcomes.index("Yes")
        yes_price = float(prices[yes_idx])
        bid = float(raw.get("bestBid") or 0) or None
        ask = float(raw.get("bestAsk") or 0) or None
        spread = (ask - bid) if (bid is not None and ask is not None) else None
        return {
            "venue":      "polymarket",
            "title":      raw["question"],
            "yes_price":  yes_price,
            "bid":        bid,
            "ask":        ask,
            "spread":     spread,
            "volume_24h": float(raw.get("volume24hr") or 0),
            "close_time": raw.get("endDate") or "",
        }
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def normalize_kalshi(raw: dict):
    """Kalshi market row (cent prices) → common shape, or None."""
    try:
        bid = float(raw["yes_bid"]) / 100.0
        ask = float(raw["yes_ask"]) / 100.0
        return {
            "venue":      "kalshi",
            "title":      raw["title"],
            "yes_price":  (bid + ask) / 2.0,
            "bid":        bid,
            "ask":        ask,
            "spread":     ask - bid,
            "volume_24h": float(raw.get("volume_24h") or 0),
            "close_time": raw.get("close_time") or "",
        }
    except (KeyError, TypeError, ValueError):
        return None


def find_overlaps(poly_rows: list, kalshi_rows: list,
                    match_ratio: float = MATCH_RATIO) -> list:
    """Fuzzy-match markets across venues; compute the yes-price gap.

    flagged=True only when |gap| > GAP_FLOOR AND both sides show 24h
    volume — a stale, volumeless book quoting a wide gap is not evidence.
    """
    overlaps = []
    for p in poly_rows:
        best, best_ratio = None, 0.0
        pt = _norm_title(p["title"])
        for k in kalshi_rows:
            ratio = difflib.SequenceMatcher(None, pt,
                                              _norm_title(k["title"])).ratio()
            if ratio > best_ratio:
                best, best_ratio = k, ratio
        if best is None or best_ratio < match_ratio:
            continue
        gap = p["yes_price"] - best["yes_price"]
        overlaps.append({
            "poly_title":   p["title"],
            "kalshi_title": best["title"],
            "match_ratio":  round(best_ratio, 3),
            "poly_yes":     p["yes_price"],
            "kalshi_yes":   best["yes_price"],
            "gap":          gap,
            "flagged":      (abs(gap) > GAP_FLOOR
                              and p["volume_24h"] > 0
                              and best["volume_24h"] > 0),
        })
    overlaps.sort(key=lambda o: -abs(o["gap"]))
    return overlaps


def log_rows(rows: list, path: Path = _LOG_PATH, ts: str | None = None) -> None:
    entry = {"ts": ts or datetime.now(timezone.utc).isoformat(),
              "rows": rows}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _fetch(url: str, extract) -> list:
    try:
        r = requests.get(url, timeout=20,
                          headers={"User-Agent": "crypto-bot-research/1.0"})
        if r.status_code != 200:
            print(f"WARN: {url.split('/')[2]} HTTP {r.status_code}")
            return []
        return extract(r.json())
    except (requests.RequestException, ValueError) as e:
        print(f"WARN: fetch failed for {url.split('/')[2]}: {e}")
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    poly_raw = _fetch(_POLY_URL, lambda j: j if isinstance(j, list) else [])
    kalshi_raw = _fetch(_KALSHI_URL,
                          lambda j: j.get("markets", []) if isinstance(j, dict) else [])

    poly = [r for r in map(normalize_polymarket, poly_raw) if r]
    kalshi = [r for r in map(normalize_kalshi, kalshi_raw) if r]

    log_rows(poly + kalshi)
    overlaps = find_overlaps(poly, kalshi)
    flagged = [o for o in overlaps if o["flagged"]]

    from routine_stamps import stamp
    stamp("prediction_scan")

    if args.quiet:
        print(f"scan: {len(poly)} polymarket + {len(kalshi)} kalshi rows, "
               f"{len(overlaps)} overlaps, {len(flagged)} flagged -> "
               f"{_LOG_PATH.name}")
        return 0

    print(f"Polymarket rows: {len(poly)}   Kalshi rows: {len(kalshi)}   "
           f"overlaps: {len(overlaps)}")
    print(FEE_CAVEAT)
    for o in overlaps[: args.top]:
        mark = " *FLAG*" if o["flagged"] else ""
        print(f"  gap {o['gap']:+.3f}{mark}  poly {o['poly_yes']:.2f} vs "
               f"kalshi {o['kalshi_yes']:.2f}  (match {o['match_ratio']})")
        print(f"    P: {o['poly_title'][:70]}")
        print(f"    K: {o['kalshi_title'][:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
