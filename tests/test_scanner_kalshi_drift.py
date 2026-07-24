"""Jul 24 2026 — Kalshi rows: 0 while the endpoint serves markets.

curl against _KALSHI_URL returned HTTP 200 with a populated markets
list, yet 19 consecutive scanner runs logged 0 kalshi rows — meaning
normalize_kalshi was dropping EVERY row and the scanner couldn't tell
"API gave nothing" from "we killed everything in normalization".

Two fixes under test:
  1. normalize_kalshi survives the two likely drifts — yes_bid/yes_ask
     None on unquoted books (fall back to last_price) and title moved
     (fall back yes_sub_title -> ticker).
  2. A schema-drift warning fires when raw rows exist but 0 survive,
     dumping the first row's field names so the next scheduled run
     self-diagnoses.

Run: python -m pytest tests/test_scanner_kalshi_drift.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

from tools.prediction_scanner import normalize_kalshi, normalize_report


def test_normalize_kalshi_happy_path_unchanged():
    row = normalize_kalshi({"title": "Will X happen?", "yes_bid": 40,
                              "yes_ask": 44, "volume_24h": 100,
                              "close_time": "2026-08-01T00:00:00Z"})
    assert row["venue"] == "kalshi"
    assert row["yes_price"] == pytest.approx(0.42)
    assert row["spread"] == pytest.approx(0.04)


def test_normalize_kalshi_none_bid_falls_back_to_last_price():
    """Unquoted book: yes_bid/yes_ask None must not kill the row when
    last_price exists."""
    row = normalize_kalshi({"title": "Will X happen?", "yes_bid": None,
                              "yes_ask": None, "last_price": 37,
                              "volume_24h": 5})
    assert row is not None
    assert row["yes_price"] == 0.37
    assert row["bid"] is None and row["ask"] is None


def test_normalize_kalshi_title_fallbacks():
    row = normalize_kalshi({"yes_sub_title": "Above 45", "yes_bid": 10,
                              "yes_ask": 12})
    assert row is not None
    assert row["title"] == "Above 45"
    row2 = normalize_kalshi({"ticker": "KXFOO-26", "yes_bid": 10,
                               "yes_ask": 12})
    assert row2["title"] == "KXFOO-26"


def test_normalize_kalshi_truly_unusable_returns_none():
    assert normalize_kalshi({"yes_bid": None, "yes_ask": None}) is None
    assert normalize_kalshi({}) is None


# ─── Jul 24 drift confirmed by the field dump: dollars-suffixed schema ──
# WARN showed last_price_dollars / liquidity_dollars — Kalshi moved from
# integer-cent fields to *_dollars decimals (served as strings).

def test_normalize_kalshi_dollars_schema():
    row = normalize_kalshi({"title": "Will X happen?",
                              "yes_bid_dollars": "0.40",
                              "yes_ask_dollars": "0.44",
                              "volume_24h": 10})
    assert row is not None
    assert row["yes_price"] == pytest.approx(0.42)
    assert row["spread"] == pytest.approx(0.04)


def test_normalize_kalshi_dollars_last_price_fallback():
    row = normalize_kalshi({"ticker": "KXFOO-26",
                              "last_price_dollars": "0.37"})
    assert row is not None
    assert row["yes_price"] == pytest.approx(0.37)
    assert row["bid"] is None and row["ask"] is None


def test_normalize_kalshi_dollars_field_wins_over_cents():
    """When both schemas coexist during a migration, the unambiguous
    dollars field must win."""
    row = normalize_kalshi({"title": "T", "yes_bid_dollars": "0.40",
                              "yes_bid": 99, "yes_ask_dollars": "0.44",
                              "yes_ask": 99})
    assert row["yes_price"] == pytest.approx(0.42)


def test_normalize_report_flags_total_kill(capsys):
    raw = [{"ticker": "T1", "weird_field": 1, "another": 2}]
    normalize_report("kalshi", raw, [])
    out = capsys.readouterr().out
    assert "WARN" in out
    assert "schema drift" in out
    assert "ticker" in out          # dumps field names for diagnosis


def test_normalize_report_silent_when_rows_survive(capsys):
    normalize_report("kalshi", [{"a": 1}], [{"venue": "kalshi"}])
    assert capsys.readouterr().out == ""


def test_normalize_report_silent_when_api_empty(capsys):
    normalize_report("kalshi", [], [])
    assert capsys.readouterr().out == ""
