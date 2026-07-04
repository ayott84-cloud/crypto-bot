"""Breakout replay/live parity (P5 follow-up).

The live breakout bot (P3.3) runs: breakeven ratchet → check_breakout_exit
→ close-based trailing exit. replay_breakout modeled only the middle one,
and — unlike replay_scalp/replay_crossover — never subtracted the 0.15%
round-trip cost. Step-1 gate decisions must describe the deployed
strategy, after costs.

Run: python -m pytest tests/test_breakout_replay_parity.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


def _fixture_df():
    """LONG breakout → 3-ATR run-up → 1.2-ATR retrace that must trigger
    the trailing exit but neither the Donchian-exit channel (exit period
    set huge) nor the ATR stop (mult set huge)."""
    closes, highs, lows = [], [], []

    def add(c, rng):
        closes.append(c)
        highs.append(c + rng / 2)
        lows.append(c - rng / 2)

    base = 100.0
    for i in range(50):                      # quiet warmup, ATR ≈ 1
        add(base + (0.3 if i % 2 else -0.3), 1.0)
    for c in (104.0, 106.0, 108.0):          # breakout, range expands
        add(c, 3.0)
    for c in (110.0, 112.0, 114.0):          # run-up (arms the trail)
        add(c, 2.0)
    for c in (112.5, 111.5, 110.5, 110.0):   # retrace ≈ mark − >1 ATR
        add(c, 2.0)

    n = len(closes)
    idx = pd.date_range("2026-06-01", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "open":   [c - 0.1 for c in closes],
        "high":   highs,
        "low":    lows,
        "close":  closes,
        "volume": [1000.0] * n,
        "close_time": [int(ts.value // 10**6) for ts in idx],
    }, index=idx)
    return df


def _cfg(use_trailing):
    return {
        "symbol": "TESTUSDT", "interval": "1h",
        "donchian_period": 10,
        "donchian_exit_period": 300,     # exit channel unreachable
        "atr_period": 5, "atr_sma_period": 5,
        "adx_period": 14, "adx_threshold": 0, "adx_exit_threshold": 0,
        "sl_atr_mult": 10.0,             # ATR stop unreachable
        "use_volume_filter": False,
        "use_trend_filter": False,
        "use_regime_gate": False,
        "use_breakeven_after_tp1": False,
        "allow_pyramiding": False,
        "allow_short": False,
        "use_funding_veto": False,
        "use_trailing_exit": use_trailing,
        "trail_arm_atr_mult": 1.5,
        "trail_atr_mult": 1.0,
        "strategy_name": "TEST Breakout",
    }


def test_replay_models_trailing_exit():
    from tools.backtest_replay import replay_breakout
    df = _fixture_df()
    rep = replay_breakout("TEST", _cfg(use_trailing=True),
                            pre_fetched_df=df)
    reasons = [t.exit_reason for t in rep.trades]
    assert "Trailing Exit" in reasons


def test_replay_trailing_off_leaves_position_open():
    from tools.backtest_replay import replay_breakout
    df = _fixture_df()
    rep = replay_breakout("TEST", _cfg(use_trailing=False),
                            pre_fetched_df=df)
    assert all(t.exit_reason != "Trailing Exit" for t in rep.trades)


def test_replay_breakout_applies_cost_model():
    from tools.backtest_replay import replay_breakout
    df = _fixture_df()
    free = replay_breakout("TEST", _cfg(True), pre_fetched_df=df,
                             round_trip_cost_pct=0.0)
    paid = replay_breakout("TEST", _cfg(True), pre_fetched_df=df,
                             round_trip_cost_pct=0.15)
    assert free.trades and paid.trades
    assert free.trades[0].pnl_pct - paid.trades[0].pnl_pct == pytest.approx(0.15)
