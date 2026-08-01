"""Module 2 — sub-window testing and the buy-and-hold benchmark.

Two questions the first full validation run raised and could not answer.

1. IS THE EDGE STILL THERE? The literature says short-term reversion has
   decayed since ~2013, and our window opens in 2001 — so a dead recent
   decade can hide inside a great 25-year number. That is precisely the
   scalp failure: PF 1.54 on one year, PF 0.95 over the full 2.85.
   --since lets the same gates run on the recent window.

2. IS IT ALPHA OR BETA? SPY_TREND returned +214.7% over 25 years. Good,
   until you ask what simply holding SPY returned over the same window.
   A strategy that underperforms buy-and-hold while claiming a Sharpe
   edge is making a drawdown argument, and that argument has to be
   stated explicitly rather than implied.

Run: python -m pytest tests/test_stock_validation_windows.py -v
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

import tools.validate_stock_sleeves as vs


def _frame(n=500, start="2010-01-01", rate=0.0004):
    idx = pd.bdate_range(start, periods=n)
    closes = 100 * np.cumprod(np.full(n, 1 + rate))
    return pd.DataFrame({
        "open": closes, "high": closes * 1.005, "low": closes * 0.995,
        "close": closes, "volume": 1e6,
    }, index=idx)


# ─── Window slicing ───────────────────────────────────────────────────────

def test_slice_since_trims_the_frame():
    # 5000 business days from 2005 reaches ~2024 — the frame must
    # actually SPAN the cutoff or the slice is trivially empty.
    df = _frame(5000, "2005-01-03")
    out = vs.slice_since({"SPY": df}, date(2013, 1, 1))["SPY"]
    assert out.index[0].date() >= date(2013, 1, 1)
    assert out.index[-1] == df.index[-1]
    assert len(out) < len(df)


def test_slice_since_none_is_a_passthrough():
    df = _frame(200)
    assert len(vs.slice_since({"SPY": df}, None)["SPY"]) == 200


def test_slice_since_drops_symbols_left_too_short():
    """A symbol that listed after the cutoff cannot support a 200-day
    SMA; keeping it would produce a zero-trade 'result' that reads as a
    verdict rather than as absent data."""
    long_df = _frame(5000, "2005-01-03")
    short_df = _frame(60, "2026-01-02")
    out = vs.slice_since({"SPY": long_df, "NEW": short_df},
                           date(2013, 1, 1), min_bars=250)
    assert "SPY" in out
    assert "NEW" not in out


# ─── Buy-and-hold benchmark ───────────────────────────────────────────────

def test_buy_and_hold_return_and_drawdown():
    df = _frame(500, rate=0.001)
    bh = vs.buy_and_hold(df)
    assert bh["total_return_pct"] > 0
    assert bh["max_dd_pct"] == pytest.approx(0.0, abs=0.01)   # monotonic


def test_buy_and_hold_measures_a_real_drawdown():
    idx = pd.bdate_range("2020-01-01", periods=300)
    closes = np.concatenate([np.linspace(100, 200, 100),
                               np.linspace(200, 120, 100),
                               np.linspace(120, 260, 100)])
    df = pd.DataFrame({"open": closes, "high": closes, "low": closes,
                        "close": closes, "volume": 1e6}, index=idx)
    bh = vs.buy_and_hold(df)
    assert bh["max_dd_pct"] == pytest.approx(40.0, abs=1.0)
    assert bh["total_return_pct"] == pytest.approx(160.0, abs=1.0)


def test_buy_and_hold_handles_empty():
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    bh = vs.buy_and_hold(empty)
    assert bh["total_return_pct"] == 0.0
    assert bh["max_dd_pct"] == 0.0


def test_benchmark_symbol_per_sleeve():
    """Each sleeve is compared against the thing an operator would
    otherwise have simply held."""
    assert vs.benchmark_symbol("trend", "SPY_TREND") == "SPY"
    assert vs.benchmark_symbol("rev", "QQQ_REV") == "QQQ"
    assert vs.benchmark_symbol("dual", "GEM") == "SPY"
