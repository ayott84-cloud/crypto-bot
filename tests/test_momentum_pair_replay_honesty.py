"""Momentum + pair replay honesty (queued Step-1 revalidation prep).

replay_momentum exited on bar CLOSES (liberal fills — the exact bias
P2.1 killed for scalp/crossover) and neither momentum nor pair deducted
costs. Fixes under test:
  - signals.exit_levels(): single source for SL/TP1/TP2/stale levels,
    shared by check_exit_conditions and the replay.
  - replay_momentum: conservative intra-bar fills (SL-first), cost
    model, pre_fetched_df + source params.
  - replay_pair: TWO-leg cost model (2x round trip per pair trade).

Run: python -m pytest tests/test_momentum_pair_replay_honesty.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


# ─── signals.exit_levels — single source of exit-level math ───────────────

_CFG = {"sl_atr_mult": 1.0, "tp1_atr_mult": 1.5, "tp2_atr_mult": 3.0,
         "stale_bars": 50, "stale_threshold_mult": 0.5,
         "use_breakeven_after_tp1": True}


def test_exit_levels_full_phase():
    from signals import exit_levels
    lv = exit_levels(100.0, 2.0, "full", _CFG)
    assert lv["sl"] == pytest.approx(98.0)
    assert lv["sl_reason"] == "SL Hit"
    assert lv["tp1"] == pytest.approx(103.0)
    assert lv["tp2"] == pytest.approx(106.0)


def test_exit_levels_breakeven_after_tp1():
    from signals import exit_levels
    lv = exit_levels(100.0, 2.0, "tp1_taken", _CFG)
    assert lv["sl"] == pytest.approx(100.0)
    assert lv["sl_reason"] == "BE Hit"


def test_check_exit_conditions_agrees_with_levels():
    """The refactor must not change live behavior: decisions match the
    published levels exactly."""
    from signals import check_exit_conditions, exit_levels
    lv = exit_levels(100.0, 2.0, "full", _CFG)
    assert check_exit_conditions(100.0, 2.0, lv["sl"] - 0.01, 1, "full", _CFG)[0] == "SL Hit"
    assert check_exit_conditions(100.0, 2.0, lv["tp1"] + 0.01, 1, "full", _CFG)[0] == "TP1 Hit"
    lv2 = exit_levels(100.0, 2.0, "tp1_taken", _CFG)
    assert check_exit_conditions(100.0, 2.0, lv2["sl"] - 0.01, 1, "tp1_taken", _CFG)[0] == "BE Hit"
    assert check_exit_conditions(100.0, 2.0, lv2["tp2"] + 0.01, 1, "tp1_taken", _CFG)[0] == "TP2 Hit"


# ─── replay_momentum — conservative intra-bar fills + costs ────────────────

def _momentum_cfg():
    from config import ASSETS
    cfg = dict(next(iter(ASSETS.values())))
    cfg.update(_CFG)
    return cfg


def _mk_df(n, closes=None, highs=None, lows=None):
    base = [100.0] * n if closes is None else closes
    return pd.DataFrame({
        "open":   base,
        "close":  base,
        "high":   [c + 0.5 for c in base] if highs is None else highs,
        "low":    [c - 0.5 for c in base] if lows is None else lows,
        "volume": [1000.0] * n,
        "close_time": list(range(n)),
    })


def _force_entry_at(monkeypatch, bar_index):
    import signals
    def fake(window, cfg, btc_close=None, btc_ema=None):
        return {"would_enter": len(window) == bar_index + 1,
                 "direction": "LONG", "blocked_by": None, "filters": {}}
    monkeypatch.setattr(signals, "analyze_entry_signal", fake)


def test_momentum_ambiguous_bar_fills_sl_first(monkeypatch):
    """A bar touching BOTH SL and TP1 books the SL at its trigger price
    (conservative), never a close-price fill."""
    from tools.backtest_replay import replay_momentum
    n, entry_bar = 90, 75
    df = _mk_df(n)
    # bar 76 wicks through both legs (ATR≈1: sl=99, tp1=101.5), closes flat
    df.loc[76, "high"] = 102.5
    df.loc[76, "low"]  = 98.5
    _force_entry_at(monkeypatch, entry_bar)
    rep = replay_momentum("TEST", _momentum_cfg(), pre_fetched_df=df,
                            round_trip_cost_pct=0.0)
    assert len(rep.trades) == 1
    t = rep.trades[0]
    assert t.exit_reason == "SL Hit"
    assert t.exit_price == pytest.approx(99.0, abs=0.15)   # trigger, not close
    assert t.pnl_pct < 0


def test_momentum_costs_deducted_per_leg(monkeypatch):
    from tools.backtest_replay import replay_momentum
    n, entry_bar = 90, 75
    df = _mk_df(n)
    df.loc[76, "high"] = 102.5
    df.loc[76, "low"]  = 98.5
    _force_entry_at(monkeypatch, entry_bar)
    free = replay_momentum("TEST", _momentum_cfg(), pre_fetched_df=df,
                             round_trip_cost_pct=0.0)
    paid = replay_momentum("TEST", _momentum_cfg(), pre_fetched_df=df,
                             round_trip_cost_pct=0.15)
    assert free.trades and paid.trades
    assert free.trades[0].pnl_pct - paid.trades[0].pnl_pct == pytest.approx(0.15)


# ─── replay_pair — two-leg cost model ──────────────────────────────────────

def test_pair_costs_are_double_leg(monkeypatch):
    import pair_signals
    from tools.backtest_replay import replay_pair

    n = 40
    eth = _mk_df(n, closes=[100.0 + i * 0.1 for i in range(n)])
    btc = _mk_df(n, closes=[50000.0] * n)

    def fake_entry(e_win, b_win, cfg):
        return {"would_enter": len(e_win) == 35,
                 "direction": "LONG_ETH_SHORT_BTC"}
    def fake_exit(e_win, b_win, position_direction, bars_held,
                    entry_ratio, cfg):
        return ("Z Reverted", "full") if bars_held >= 2 else (None, None)
    monkeypatch.setattr(pair_signals, "analyze_pair_entry", fake_entry)
    monkeypatch.setattr(pair_signals, "check_pair_exit", fake_exit)

    free = replay_pair(asset_name="TESTPAIR", pre_fetched=(eth, btc),
                         round_trip_cost_pct=0.0)
    paid = replay_pair(asset_name="TESTPAIR", pre_fetched=(eth, btc),
                         round_trip_cost_pct=0.15)
    assert free.trades and paid.trades
    # A pair trade is TWO round trips — costs deduct twice
    assert free.trades[0].pnl_pct - paid.trades[0].pnl_pct == pytest.approx(0.30)
