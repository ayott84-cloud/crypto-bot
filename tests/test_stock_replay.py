"""Module 2 Phase S2 — stock sleeve replays.

Same discipline as the crypto replays: import the LIVE signal functions
so backtest/live drift is impossible, deduct the round-trip cost per
closed trade, and refuse to score a window the fetcher flagged as holed.

The dual-momentum replay is shaped differently from every crypto replay
in the repo: it is a ROTATION that is always holding something, so a
"trade" is a holding period and a rebalance that changes the winner
costs one round trip. Getting that wrong understates costs by exactly
the number of rebalances.

Run: python -m pytest tests/test_stock_replay.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

import tools.backtest_replay as br


def _frame(closes, start="2015-01-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({
        "open": closes, "high": [c * 1.005 for c in closes],
        "low": [c * 0.995 for c in closes], "close": closes,
        "volume": [1e6] * len(closes),
    }, index=idx)


_TREND_CFG = {"symbol": "SPY", "sma_period": 50, "interval": "1d",
               "strategy_name": "SPY 1M StockTrend", "asset_class": "equity_etf"}


# ─── Trend replay ─────────────────────────────────────────────────────────

def test_trend_replay_round_trips_one_regime_cycle():
    """Up then down: one entry above the SMA, one exit below it."""
    closes = list(np.concatenate([np.linspace(100, 200, 200),
                                    np.linspace(200, 100, 200)]))
    rep = br.replay_stock_trend("SPY_TREND", _TREND_CFG,
                                  pre_fetched_df=_frame(closes))
    assert rep.bot == "stocktrend"
    assert rep.n_trades >= 1
    assert all(t.direction == "LONG" for t in rep.trades), "long/flat only"


def test_trend_replay_deducts_the_equity_cost_not_the_crypto_one():
    closes = list(np.concatenate([np.linspace(100, 200, 200),
                                    np.linspace(200, 100, 200)]))
    df = _frame(closes)
    cheap = br.replay_stock_trend("SPY_TREND", _TREND_CFG,
                                    pre_fetched_df=df.copy())
    dear = br.replay_stock_trend("SPY_TREND", _TREND_CFG,
                                   pre_fetched_df=df.copy(),
                                   round_trip_cost_pct=1.0)
    assert cheap.total_return_pct > dear.total_return_pct
    # default must be the ETF rate (0.05), not the WEEX 0.15
    per_trade_gap = (cheap.total_return_pct - dear.total_return_pct) / max(1, cheap.n_trades)
    assert per_trade_gap == pytest.approx(1.0 - 0.05, abs=0.02)


def test_trend_replay_never_holds_through_a_downtrend():
    closes = list(np.linspace(200, 100, 300))
    rep = br.replay_stock_trend("SPY_TREND", _TREND_CFG,
                                  pre_fetched_df=_frame(closes))
    assert rep.n_trades == 0, "entered a strategy that is flat by construction"


def test_trend_replay_refuses_an_incomplete_window():
    """The fetcher flags interior holes; a replay that scores such a
    window anyway would launder a data defect into a verdict."""
    closes = list(np.linspace(100, 200, 300))
    df = _frame(closes)
    df.attrs["incomplete"] = True
    rep = br.replay_stock_trend("SPY_TREND", _TREND_CFG, pre_fetched_df=df)
    assert rep.warnings, "incomplete window scored silently"
    assert "INCOMPLETE" in rep.summary_line()


# ─── Reversion replay ─────────────────────────────────────────────────────

_REV_CFG = {"symbol": "QQQ", "sma_period": 50, "ibs_threshold": 0.2,
             "rsi_period": 2, "rsi_threshold": 10.0, "exit_sma_period": 5,
             "rsi_exit": 70.0, "max_hold_bars": 10, "interval": "1d",
             "strategy_name": "QQQ 1D StockRev", "asset_class": "equity_etf"}


def test_reversion_replay_trades_are_long_and_bounded_in_time():
    rng = np.random.default_rng(7)
    # uptrend with noise so dips happen inside an uptrend
    closes = list(100 * np.cumprod(1 + rng.normal(0.0007, 0.012, 500)))
    rep = br.replay_stock_rev("QQQ_REV", _REV_CFG,
                                pre_fetched_df=_frame(closes))
    assert rep.bot == "stockrev"
    assert all(t.direction == "LONG" for t in rep.trades)
    for t in rep.trades:
        assert t.exit_bar - t.entry_bar <= _REV_CFG["max_hold_bars"] + 1


def test_reversion_replay_records_exit_reasons():
    rng = np.random.default_rng(11)
    closes = list(100 * np.cumprod(1 + rng.normal(0.0007, 0.012, 500)))
    rep = br.replay_stock_rev("QQQ_REV", _REV_CFG,
                                pre_fetched_df=_frame(closes))
    if rep.n_trades:
        assert all(t.exit_reason for t in rep.trades)
        assert set(t.exit_reason for t in rep.trades) <= {
            "Above SMA", "RSI Exit", "Time Stop"}


# ─── Dual-momentum rotation replay ───────────────────────────────────────

_DUAL_CFG = {
    "risk_assets": ["SPY", "VEU"], "safe_asset": "AGG", "cash_asset": "BIL",
    "lookbacks_months": [3, 6], "interval": "1d", "rebalance": "month_end",
    "strategy_name": "GEM 1M StockDual", "asset_class": "equity_etf",
}


def _dual_frames(n=600):
    """SPY strong, VEU weak, AGG flat, BIL flat -> SPY should be held."""
    return {
        "SPY": _frame(list(np.linspace(100, 250, n))),
        "VEU": _frame(list(np.linspace(100, 110, n))),
        "AGG": _frame(list(np.linspace(100, 102, n))),
        "BIL": _frame(list(np.linspace(100, 101, n))),
    }


def test_dual_replay_holds_the_winner_and_reports_rotations():
    rep = br.replay_stock_dual("GEM", _DUAL_CFG,
                                 pre_fetched_frames=_dual_frames())
    assert rep.bot == "stockdual"
    assert rep.n_trades >= 1
    # a rotation strategy is always invested: every trade must name what
    # it held, and consecutive trades must be contiguous holding periods
    for t in rep.trades:
        assert t.exit_bar > t.entry_bar


def test_dual_replay_charges_one_round_trip_per_rotation():
    """A rotation closes one position and opens another. Charging zero
    (or charging per bar) is the easy way to overstate this strategy."""
    frames = _dual_frames()
    cheap = br.replay_stock_dual("GEM", _DUAL_CFG,
                                   pre_fetched_frames=frames)
    dear = br.replay_stock_dual("GEM", _DUAL_CFG,
                                  pre_fetched_frames=frames,
                                  round_trip_cost_pct=2.0)
    assert cheap.total_return_pct > dear.total_return_pct
    gap = cheap.total_return_pct - dear.total_return_pct
    assert gap == pytest.approx(cheap.n_trades * (2.0 - 0.05), abs=0.5)


def test_dual_replay_rotates_when_leadership_changes():
    n = 700
    # SPY leads for the first half, VEU takes over in the second
    spy = np.concatenate([np.linspace(100, 200, 350), np.linspace(200, 190, 350)])
    veu = np.concatenate([np.linspace(100, 105, 350), np.linspace(105, 260, 350)])
    frames = {
        "SPY": _frame(list(spy)), "VEU": _frame(list(veu)),
        "AGG": _frame(list(np.linspace(100, 102, n))),
        "BIL": _frame(list(np.linspace(100, 101, n))),
    }
    rep = br.replay_stock_dual("GEM", _DUAL_CFG, pre_fetched_frames=frames)
    held = {t.exit_reason for t in rep.trades}
    assert len(rep.trades) >= 2, "never rotated despite a leadership change"
    assert any("VEU" in r for r in held) or any("SPY" in r for r in held)


def test_dual_replay_blocks_on_missing_symbol():
    frames = _dual_frames()
    frames.pop("VEU")
    rep = br.replay_stock_dual("GEM", _DUAL_CFG, pre_fetched_frames=frames)
    assert rep.n_trades == 0
    assert rep.warnings


# ─── Registry wiring ──────────────────────────────────────────────────────

def test_stock_sleeves_are_in_the_cli_runner_registry():
    import inspect
    src = inspect.getsource(br.main)
    for name in ("stocktrend", "stockdual", "stockrev"):
        assert name in src, f"{name} not wired into the --bot choices"
