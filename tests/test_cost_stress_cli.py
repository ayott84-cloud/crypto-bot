"""Jul 30 audit item #1 — cost-stress arm for Gate A.

The honest replays deduct 0.15% round-trip (~1.25x WEEX fees). Gate A
(backtest-expert) requires survival at 1.5-2x the realistic estimate —
so the CLI needs a --cost-pct override to rerun live passers at
0.25-0.30% without editing code. Threaded through the three live bots
(scalp / breakout / momentum); parked bots don't need a stress arm.

Run: python -m pytest tests/test_cost_stress_cli.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

import tools.backtest_replay as br


def _capture(store):
    def fake(name, cfg, bars=None, source=None, **kw):
        store.append(kw)
        return br.BacktestReport(bot="x", asset=name, bars_seen=0)
    return fake


def test_run_scalp_forwards_cost_pct(monkeypatch):
    calls = []
    monkeypatch.setattr(br, "replay_scalp", _capture(calls))
    br._run_scalp(bars=100, source="weex", cost_pct=0.30)
    assert calls
    assert all(kw.get("round_trip_cost_pct") == 0.30 for kw in calls)


def test_run_scalp_default_keeps_standard_cost(monkeypatch):
    calls = []
    monkeypatch.setattr(br, "replay_scalp", _capture(calls))
    br._run_scalp(bars=100, source="weex")
    assert calls
    assert all("round_trip_cost_pct" not in kw for kw in calls)


def test_run_breakout_forwards_cost_pct(monkeypatch):
    calls = []

    def fake(name, cfg, bars=None, source=None, regime_gate_active=False, **kw):
        calls.append(kw)
        return br.BacktestReport(bot="breakout", asset=name, bars_seen=0)

    monkeypatch.setattr(br, "replay_breakout", fake)
    br._run_breakout(bars=100, source="weex", cost_pct=0.30)
    assert calls
    assert all(kw.get("round_trip_cost_pct") == 0.30 for kw in calls)


def test_run_momentum_forwards_cost_pct(monkeypatch):
    calls = []
    monkeypatch.setattr(br, "replay_momentum", _capture(calls))
    br._run_momentum(bars=100, source="weex", assets="BTC", cost_pct=0.30)
    assert calls
    assert all(kw.get("round_trip_cost_pct") == 0.30 for kw in calls)
