"""Regression test — Phase 2C.3 bugfix.

WEEX returns klines as POSITIONAL arrays:
  [open_time_ms, open, high, low, close, volume, close_time, quote_vol,
   num_trades, taker_buy_vol, taker_buy_quote_vol]
NOT as dicts.

The first round of breakout/pair/reversal main loops and the backtest
harness all assumed dict shape and crashed with KeyError: 'close'.
This test pins the contract so the regression can't return.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


def _fake_weex_klines(n: int = 30) -> list:
    """Emit n bars in WEEX's positional format."""
    rows = []
    base_ts = 1_700_000_000_000
    for i in range(n):
        rows.append([
            base_ts + i * 60_000,
            100 + i * 0.1,
            101 + i * 0.1,
             99 + i * 0.1,
            100.5 + i * 0.1,
            1000 + i,
            base_ts + (i + 1) * 60_000,
            100_000,
            50,
            500,
            50_000,
        ])
    return rows


def test_breakout_build_dataframe_handles_positional_klines():
    import breakout_main
    df = breakout_main._build_dataframe(_fake_weex_klines(30))
    assert "close" in df.columns
    assert len(df) == 30
    assert df["close"].iloc[0] == pytest.approx(100.5)


def test_breakout_build_dataframe_handles_empty():
    import breakout_main
    df = breakout_main._build_dataframe([])
    assert df.empty
    assert "close" in df.columns


def test_reversal_build_dataframe_handles_positional_klines():
    import reversal_main
    df = reversal_main._build_dataframe(_fake_weex_klines(30))
    assert "close" in df.columns
    assert len(df) == 30


def test_reversal_build_dataframe_handles_empty():
    import reversal_main
    df = reversal_main._build_dataframe([])
    assert df.empty


def test_pair_closes_from_klines_handles_positional_klines():
    import pair_main
    closes = pair_main._closes_from_klines(_fake_weex_klines(30))
    assert len(closes) == 30
    assert closes.iloc[0] == pytest.approx(100.5)


def test_pair_closes_from_klines_handles_empty():
    import pair_main
    closes = pair_main._closes_from_klines([])
    assert closes.empty


def test_backtest_fetch_klines_handles_positional_klines(monkeypatch):
    from tools import backtest_replay
    fake = _fake_weex_klines(30)
    class _FakeExecutor:
        def __init__(self, *a, **kw): pass
        def get_klines(self, *a, **kw): return fake
    monkeypatch.setattr("executor.Executor", _FakeExecutor)
    df = backtest_replay._fetch_klines("BTCUSDT", "1h", 30)
    assert "close" in df.columns
    assert len(df) == 30
