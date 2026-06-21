"""Tier 0.2 — spread-rejection gate.

A SpreadTracker keeps a per-symbol rolling window of bid-ask spread samples
(in basis points). An "air pocket" — sudden spread blowup typical of thin
liquidity, halts, or news-driven crash dumps — is when the current spread
exceeds a multiplier × the rolling-mean. Bots gate new entries through this
to avoid eating poor fills during microstructure dislocations.

A graceful-degradation principle: if the tracker has too few samples or the
ticker fetch returns malformed data, the gate ALWAYS allows the trade
(absence of evidence is not evidence of an air pocket).

Run: python -m pytest tests/test_microstructure_spread_gate.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))


# ─── SpreadTracker — rolling-spread bookkeeping ────────────────────────────

def test_tracker_starts_empty_for_unknown_symbol():
    from microstructure import SpreadTracker
    t = SpreadTracker()
    assert t.sample_count("BTCUSDT") == 0
    assert t.current_bps("BTCUSDT") is None
    assert t.rolling_mean_bps("BTCUSDT") is None


def test_tracker_accepts_samples_and_reports_current():
    from microstructure import SpreadTracker
    t = SpreadTracker()
    t.add_sample("BTCUSDT", 2.0)
    t.add_sample("BTCUSDT", 3.0)
    assert t.sample_count("BTCUSDT") == 2
    assert t.current_bps("BTCUSDT") == 3.0


def test_tracker_computes_rolling_mean():
    from microstructure import SpreadTracker
    t = SpreadTracker()
    for s in (1.0, 2.0, 3.0, 4.0, 5.0):
        t.add_sample("BTCUSDT", s)
    assert t.rolling_mean_bps("BTCUSDT") == 3.0


def test_tracker_evicts_old_samples_beyond_window():
    """Default window=60. After 70 samples, only the last 60 remain."""
    from microstructure import SpreadTracker
    t = SpreadTracker(window=60)
    for i in range(70):
        t.add_sample("BTCUSDT", float(i))
    assert t.sample_count("BTCUSDT") == 60
    # Mean of [10, 11, ..., 69] is 39.5
    assert t.rolling_mean_bps("BTCUSDT") == 39.5


def test_tracker_isolates_symbols():
    from microstructure import SpreadTracker
    t = SpreadTracker()
    t.add_sample("BTCUSDT", 2.0)
    t.add_sample("ETHUSDT", 5.0)
    assert t.current_bps("BTCUSDT") == 2.0
    assert t.current_bps("ETHUSDT") == 5.0


# ─── is_air_pocket — the gate ──────────────────────────────────────────────

def test_is_air_pocket_false_when_below_min_samples():
    """Need a meaningful rolling history. Default min_samples=10."""
    from microstructure import SpreadTracker
    t = SpreadTracker()
    for s in (2.0, 2.5, 2.0, 100.0):  # last sample is huge but we only have 4
        t.add_sample("BTCUSDT", s)
    assert t.is_air_pocket("BTCUSDT") is False


def test_is_air_pocket_false_when_unknown_symbol():
    from microstructure import SpreadTracker
    t = SpreadTracker()
    assert t.is_air_pocket("BTCUSDT") is False


def test_is_air_pocket_true_when_current_exceeds_multiplier_x_mean():
    from microstructure import SpreadTracker
    t = SpreadTracker()
    # Seed 10 calm samples averaging 2 bps
    for _ in range(9):
        t.add_sample("BTCUSDT", 2.0)
    # 10th sample is the current — 10x the calm mean
    t.add_sample("BTCUSDT", 20.0)
    # Default multiplier=3.0; current_bps (20) > 3 × rolling_mean (...)
    # rolling_mean over all 10 samples = (9*2 + 20)/10 = 3.8
    # 20 > 3 * 3.8 == 11.4 → air pocket
    assert t.is_air_pocket("BTCUSDT") is True


def test_is_air_pocket_false_when_current_below_multiplier():
    from microstructure import SpreadTracker
    t = SpreadTracker()
    for _ in range(10):
        t.add_sample("BTCUSDT", 2.0)
    t.add_sample("BTCUSDT", 4.0)
    # current=4, mean=(10*2 + 4)/11 ~= 2.18, 4 < 3 * 2.18 ~= 6.54 → not air pocket
    assert t.is_air_pocket("BTCUSDT") is False


def test_is_air_pocket_honors_custom_multiplier():
    from microstructure import SpreadTracker
    t = SpreadTracker()
    for _ in range(10):
        t.add_sample("BTCUSDT", 2.0)
    t.add_sample("BTCUSDT", 4.5)
    # current=4.5, mean=~2.22, 4.5 > 2.0 * 2.22 = 4.45 → True with mult=2
    assert t.is_air_pocket("BTCUSDT", multiplier=2.0) is True
    assert t.is_air_pocket("BTCUSDT", multiplier=3.0) is False


def test_is_air_pocket_zero_mean_safe():
    """If rolling mean is exactly 0 (degenerate), don't divide by zero —
    treat as 'no signal, allow entry'."""
    from microstructure import SpreadTracker
    t = SpreadTracker()
    for _ in range(11):
        t.add_sample("BTCUSDT", 0.0)
    # Even a positive current sample shouldn't flag an air pocket against 0 mean.
    assert t.is_air_pocket("BTCUSDT") is False


# ─── fetch_spread — graceful degradation on missing fields ─────────────────

def test_fetch_spread_returns_bps_from_ticker_bid_ask():
    """Standard ticker24h shape returns bid/ask; helper computes bps."""
    from microstructure import fetch_spread_bps
    executor = MagicMock()
    # mid = 100, ask-bid = 0.01 → 1 bp
    executor.get_ticker_24h.return_value = [{
        "symbol": "BTCUSDT",
        "bidPrice": "99.995",
        "askPrice": "100.005",
    }]
    bps = fetch_spread_bps(executor, "BTCUSDT")
    assert bps is not None
    assert abs(bps - 1.0) < 0.001  # 0.01 / 100 * 10000 = 1 bps


def test_fetch_spread_returns_none_on_empty_ticker():
    from microstructure import fetch_spread_bps
    executor = MagicMock()
    executor.get_ticker_24h.return_value = []
    assert fetch_spread_bps(executor, "BTCUSDT") is None


def test_fetch_spread_returns_none_on_missing_bid_ask_fields():
    from microstructure import fetch_spread_bps
    executor = MagicMock()
    executor.get_ticker_24h.return_value = [{
        "symbol": "BTCUSDT",
        "lastPrice": "100.0",
        # bidPrice / askPrice intentionally absent
    }]
    assert fetch_spread_bps(executor, "BTCUSDT") is None


def test_fetch_spread_returns_none_on_zero_or_inverted_quotes():
    """Defensive: if bid >= ask or either is 0 (stale snapshot), bail."""
    from microstructure import fetch_spread_bps
    executor = MagicMock()
    executor.get_ticker_24h.return_value = [{
        "bidPrice": "100.0",
        "askPrice": "99.99",   # inverted (crossed market) — bail
    }]
    assert fetch_spread_bps(executor, "BTCUSDT") is None


def test_fetch_spread_returns_none_on_executor_exception():
    """Network failure → None → gate degrades to allow entry."""
    from microstructure import fetch_spread_bps
    executor = MagicMock()
    executor.get_ticker_24h.side_effect = RuntimeError("network unreachable")
    assert fetch_spread_bps(executor, "BTCUSDT") is None


# ─── fetch_all_spreads_bps — batch fetch (one API call/cycle) ──────────────

def test_fetch_all_spreads_returns_map_for_multiple_symbols():
    """One ticker24h(None) call returns all symbols; helper produces map."""
    from microstructure import fetch_all_spreads_bps
    executor = MagicMock()
    executor.get_ticker_24h.return_value = [
        {"symbol": "BTCUSDT", "bidPrice": "99.995",  "askPrice": "100.005"},
        {"symbol": "ETHUSDT", "bidPrice": "999.9",    "askPrice": "1000.1"},
    ]
    result = fetch_all_spreads_bps(executor)
    assert set(result.keys()) == {"BTCUSDT", "ETHUSDT"}
    assert abs(result["BTCUSDT"] - 1.0) < 0.001
    # ETH: mid=1000, spread=0.2, bps = 0.2/1000 * 10000 = 2 bps
    assert abs(result["ETHUSDT"] - 2.0) < 0.001
    # Only one call (batch)
    executor.get_ticker_24h.assert_called_once_with(None)


def test_fetch_all_spreads_skips_malformed_rows():
    from microstructure import fetch_all_spreads_bps
    executor = MagicMock()
    executor.get_ticker_24h.return_value = [
        {"symbol": "BTCUSDT", "bidPrice": "99.995",  "askPrice": "100.005"},
        {"symbol": "BAD1",    "bidPrice": "100",      "askPrice": "99"},  # crossed
        {"symbol": "BAD2",    "lastPrice": "1.0"},                          # no bid/ask
        "not a dict",                                                       # garbage
        {"symbol": "ETHUSDT", "bidPrice": "999.9",    "askPrice": "1000.1"},
    ]
    result = fetch_all_spreads_bps(executor)
    assert set(result.keys()) == {"BTCUSDT", "ETHUSDT"}


def test_fetch_all_spreads_returns_empty_on_exception():
    from microstructure import fetch_all_spreads_bps
    executor = MagicMock()
    executor.get_ticker_24h.side_effect = RuntimeError("api down")
    assert fetch_all_spreads_bps(executor) == {}


# ─── get_default_tracker — process-level singleton ─────────────────────────

def test_get_default_tracker_returns_same_instance():
    from microstructure import get_default_tracker
    a = get_default_tracker()
    b = get_default_tracker()
    assert a is b
