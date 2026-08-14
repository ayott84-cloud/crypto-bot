"""Aug 14 2026 — the whale funnel instrumented the WRONG code path.

Jul 30 added a rejection funnel so a 30-day soak ending at n=0 would be
diagnosable. Day 30 arrived with zero trades AND zero funnel records.

Cause: whale_main.run_cycle has its own inline classification loop and
calls classify() WITHOUT funnel=. It never calls generate_signals(),
which is the function the Jul 30 work instrumented. The tests passed
because they exercised generate_signals; production used the other path.

This is the third instance of the same failure shape:
  * the replay's trailing knob that live never had (live == no_trail)
  * the static-gate replay that diverged from the live gate
  * now telemetry wired to a function the daemon does not call
The lesson each time: verify the LIVE path exercises the thing, not
just that a unit test does.

Run: python -m pytest tests/test_whale_funnel_live_path.py -v
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

import whale_main
import whale_signals as ws
from whale_signals import CoinStats


def _stats(**coins):
    return {c: CoinStats(coin=c, **kw) for c, kw in coins.items()}


@pytest.fixture(autouse=True)
def _universe(monkeypatch):
    """The classifier consults whale_universe.is_top100, which reaches for
    a market-cap list. Pin it so these tests measure the funnel, not the
    network. BTC/ETH/SOL are in; anything else is out."""
    import whale_universe
    monkeypatch.setattr(whale_universe, "is_top100",
                          lambda c: c.upper() in {"BTC", "ETH", "SOL"})


# ─── The live path must record a funnel ──────────────────────────────────

def test_live_classifier_records_a_funnel():
    smart = _stats(BTC=dict(longs=20, upnl_sum=1000.0),
                    ETH=dict(longs=1))                 # min_traders reject
    rekt = _stats(BTC=dict(longs=2, shorts=3), ETH=dict(shorts=2))
    _sigs, funnel = whale_main.classify_all(smart, rekt, weex_whitelist=None)
    assert funnel["coins"] == 2
    assert funnel.get("min_traders") == 1
    assert sum(v for k, v in funnel.items() if k != "coins") >= 1


def test_live_classifier_publishes_last_funnel():
    """log_signals_jsonl reads whale_signals.LAST_FUNNEL, so the live
    path has to set it — not just build it locally."""
    ws.LAST_FUNNEL = {}
    smart = _stats(BTC=dict(longs=1))
    whale_main.classify_all(smart, _stats(BTC=dict(shorts=1)),
                              weex_whitelist=None)
    assert ws.LAST_FUNNEL.get("coins") == 1


def test_run_cycle_uses_the_instrumented_classifier():
    """Guard against the loop being re-inlined and silently dropping the
    funnel again."""
    src = inspect.getsource(whale_main.run_cycle)
    assert "classify_all(" in src, \
        "run_cycle stopped using the instrumented classifier"


def test_funnel_counts_symbols_absent_from_weex():
    smart = _stats(BTC=dict(longs=20, upnl_sum=500.0))
    rekt = _stats(BTC=dict(shorts=3))
    _sigs, funnel = whale_main.classify_all(smart, rekt,
                                              weex_whitelist=set())
    assert funnel.get("not_on_weex") == 1


def test_universe_pruning_is_reported_separately_from_weex_listing():
    """hl_coin_to_weex_symbol returns None for both 'not on WEEX' and
    'not in the top-100 universe'. The top-100 filter is what starved
    the funding bot — the funnel has to name which one fired."""
    smart = _stats(BTC=dict(longs=20, upnl_sum=500.0),   # in universe
                    PEPE=dict(longs=20, upnl_sum=500.0))  # pruned
    rekt = _stats(BTC=dict(shorts=3), PEPE=dict(shorts=3))
    _sigs, funnel = whale_main.classify_all(smart, rekt, weex_whitelist=None)
    assert funnel.get("not_top100") == 1
    assert funnel.get("not_on_weex") is None, \
        "universe pruning was misattributed to a missing WEEX listing"


def test_zero_signal_cycle_still_yields_a_diagnosable_funnel():
    """The whole point: n=0 must come with a reason."""
    smart = _stats(BTC=dict(longs=1), ETH=dict(longs=2), SOL=dict(longs=1))
    rekt = _stats(BTC=dict(shorts=1), ETH=dict(shorts=1), SOL=dict(shorts=1))
    sigs, funnel = whale_main.classify_all(smart, rekt, weex_whitelist=None)
    assert sigs == []
    assert funnel["coins"] == 3
    assert funnel.get("min_traders") == 3, \
        "a zero-signal cycle produced no explanation"


# ─── The writer still emits it ───────────────────────────────────────────

def test_log_signals_jsonl_writes_the_funnel_record(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(whale_main, "WHALE_SIGNAL_LOG",
                          tmp_path / "whale_signals.jsonl")
    monkeypatch.setattr(ws, "LAST_FUNNEL", {"coins": 7, "min_traders": 6})
    whale_main.log_signals_jsonl([])
    rec = json.loads((tmp_path / "whale_signals.jsonl")
                      .read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["type"] == "funnel" and rec["coins"] == 7
