"""R7 — Polymarket/Kalshi READ-ONLY cross-venue spread scanner.

Fixture-based tests only; all HTTP is out of scope here (the fetchers
are thin and degrade gracefully — the analytics are the pure part).

Run: python -m pytest tests/test_prediction_scanner.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest


# ─── Normalization ─────────────────────────────────────────────────────────

def test_normalize_polymarket_row():
    from tools.prediction_scanner import normalize_polymarket
    raw = {
        "question": "Will BTC close above $70k on Dec 31?",
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(["0.62", "0.38"]),
        "bestBid": 0.61, "bestAsk": 0.63,
        "volume24hr": 12345.6,
        "endDate": "2026-12-31T23:59:59Z",
    }
    row = normalize_polymarket(raw)
    assert row["venue"] == "polymarket"
    assert row["yes_price"] == pytest.approx(0.62)
    assert row["spread"] == pytest.approx(0.02, abs=1e-9)
    assert row["volume_24h"] == pytest.approx(12345.6)


def test_normalize_kalshi_row_prices_in_cents():
    from tools.prediction_scanner import normalize_kalshi
    raw = {"title": "Will BTC close above $70k on Dec 31?",
            "yes_bid": 58, "yes_ask": 61, "volume_24h": 900,
            "close_time": "2026-12-31T23:00:00Z"}
    row = normalize_kalshi(raw)
    assert row["venue"] == "kalshi"
    assert row["yes_price"] == pytest.approx(0.595)   # mid of 0.58/0.61
    assert row["spread"] == pytest.approx(0.03, abs=1e-9)


def test_normalize_handles_garbage():
    from tools.prediction_scanner import normalize_polymarket, normalize_kalshi
    assert normalize_polymarket({"question": "x"}) is None
    assert normalize_kalshi({"title": "x"}) is None


# ─── Cross-venue matching + gap flagging ───────────────────────────────────

def _row(venue, title, yes, vol=100.0, spread=0.01):
    return {"venue": venue, "title": title, "yes_price": yes,
             "bid": yes - spread / 2, "ask": yes + spread / 2,
             "spread": spread, "volume_24h": vol, "close_time": ""}


def test_match_finds_equivalent_markets():
    from tools.prediction_scanner import find_overlaps
    poly = [_row("polymarket", "Will BTC close above $70k on Dec 31?", 0.62)]
    kalshi = [_row("kalshi", "Will BTC close above $70K on Dec 31?", 0.55)]
    overlaps = find_overlaps(poly, kalshi)
    assert len(overlaps) == 1
    o = overlaps[0]
    assert o["gap"] == pytest.approx(0.07, abs=1e-6)
    assert o["flagged"] is True     # |gap| > 0.03 and both have volume


def test_match_rejects_unrelated_titles():
    from tools.prediction_scanner import find_overlaps
    poly = [_row("polymarket", "Will BTC close above $70k on Dec 31?", 0.6)]
    kalshi = [_row("kalshi", "Will the Fed cut rates in March?", 0.4)]
    assert find_overlaps(poly, kalshi) == []


def test_small_gap_or_no_volume_not_flagged():
    from tools.prediction_scanner import find_overlaps
    poly = [_row("polymarket", "Will ETH pass $5k this year?", 0.50)]
    kalshi = [_row("kalshi", "Will ETH pass $5K this year?", 0.52)]
    o = find_overlaps(poly, kalshi)[0]
    assert o["flagged"] is False     # 0.02 gap under the 0.03 floor

    poly2 = [_row("polymarket", "Will ETH pass $5k this year?", 0.50, vol=0.0)]
    o2 = find_overlaps(poly2, kalshi)[0]
    assert o2["flagged"] is False    # no volume on one side


# ─── jsonl accumulation ────────────────────────────────────────────────────

def test_log_rows_appends_jsonl(tmp_path):
    from tools.prediction_scanner import log_rows
    out = tmp_path / "spreads.jsonl"
    log_rows([_row("kalshi", "t1", 0.5)], out, ts="2026-07-04T12:00:00Z")
    log_rows([_row("kalshi", "t2", 0.6)], out, ts="2026-07-04T13:00:00Z")
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["ts"] == "2026-07-04T12:00:00Z"
    assert first["rows"][0]["title"] == "t1"
