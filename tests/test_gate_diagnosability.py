"""Jul 30 fleet-audit item #4 — make quiet gates diagnosable.

Whale: the 30-day soak reads its gate Aug 15. If it lands at n=0, the
signal log must explain WHERE candidates died (funnel counts per
rejection reason) — today it only records signals that fire, so a
zero-signal month leaves nothing to analyze.

Funding: "Generated 0 funding-fade signals" doesn't say whether the
nearest candidate was at the 60th or 96th percentile. A proximity line
turns silence into data.

Run: python -m pytest tests/test_gate_diagnosability.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

import whale_signals
from whale_signals import CoinStats, classify


# ─── Whale classify funnel ─────────────────────────────────────────────────

def _run(smart, rekt, funnel):
    return classify("TESTCOIN", smart, rekt, "TESTUSDT", funnel=funnel)


def test_funnel_counts_min_traders():
    funnel = {}
    _run(CoinStats(coin="T", longs=1), CoinStats(coin="T"), funnel)
    assert funnel.get("min_traders") == 1


def test_funnel_counts_crowded():
    funnel = {}
    smart = CoinStats(coin="T", longs=20, upnl_sum=1000.0)
    rekt = CoinStats(coin="T", longs=10)          # both 100% long → crowded
    _run(smart, rekt, funnel)
    assert funnel.get("crowded") == 1


def test_funnel_counts_smart_underwater(monkeypatch):
    monkeypatch.setattr(whale_signals, "REQUIRE_SMART_WINNING", True)
    funnel = {}
    smart = CoinStats(coin="T", longs=20, upnl_sum=-5000.0)
    rekt = CoinStats(coin="T", shorts=5)          # not crowded
    _run(smart, rekt, funnel)
    assert funnel.get("smart_underwater") == 1


def test_funnel_counts_below_thresholds():
    funnel = {}
    smart = CoinStats(coin="T", longs=6, shorts=5, upnl_sum=1000.0)
    rekt = CoinStats(coin="T", longs=3, shorts=3)
    sig = _run(smart, rekt, funnel)
    assert sig is None
    assert funnel.get("below_thresholds") == 1


def test_funnel_counts_fired_signal():
    funnel = {}
    smart = CoinStats(coin="T", longs=20, upnl_sum=1000.0)
    rekt = CoinStats(coin="T", longs=2, shorts=3)  # not crowded
    sig = _run(smart, rekt, funnel)
    assert sig is not None
    assert funnel.get("signal") == 1


def test_classify_without_funnel_still_works():
    smart = CoinStats(coin="T", longs=20, upnl_sum=1000.0)
    rekt = CoinStats(coin="T", longs=2, shorts=3)
    assert classify("T", smart, rekt, "TUSDT") is not None


def test_generate_signals_exposes_last_funnel(monkeypatch):
    monkeypatch.setattr(whale_signals, "fetch_cohorts", lambda: ([], []))
    whale_signals.generate_signals(weex_whitelist=set())
    assert isinstance(whale_signals.LAST_FUNNEL, dict)
    assert whale_signals.LAST_FUNNEL.get("coins") == 0


def test_signal_log_writes_funnel_record_even_when_no_signals(monkeypatch):
    """A zero-signal cycle must still leave an analyzable trace."""
    import whale_main
    monkeypatch.setattr(whale_signals, "LAST_FUNNEL",
                          {"coins": 42, "min_traders": 30})
    whale_main.log_signals_jsonl([])
    log = Path(whale_main.WHALE_SIGNAL_LOG)
    assert log.exists()
    rec = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["type"] == "funnel"
    assert rec["coins"] == 42


# ─── Funding proximity ─────────────────────────────────────────────────────

def test_nearest_extreme_picks_most_extreme_tail():
    from funding_main import nearest_extreme
    near = nearest_extreme([("BTC", 60.0), ("HOME", 8.0), ("X", None)])
    assert near["coin"] == "HOME"                  # p8 → extremity 92
    assert near["extremity"] == pytest.approx(92.0)
    assert near["percentile"] == pytest.approx(8.0)


def test_nearest_extreme_empty():
    from funding_main import nearest_extreme
    assert nearest_extreme([]) is None
    assert nearest_extreme([("A", None)]) is None
