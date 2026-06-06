"""Phase 2C.2 — backtest replay harness smoke tests.

The replay itself requires network access (fetches klines from WEEX) so
these are unit tests on the in-memory replay logic with synthetic bars.

Run: python -m pytest tests/test_backtest_replay.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


def test_module_imports_cleanly():
    from tools import backtest_replay
    assert hasattr(backtest_replay, "replay_breakout")
    assert hasattr(backtest_replay, "replay_pair")
    assert hasattr(backtest_replay, "replay_reversal")


def test_trade_result_is_win_sign_aware():
    from tools.backtest_replay import TradeResult
    win  = TradeResult("LONG", 0, 5, 100, 110, "TP1", +10.0)
    lose = TradeResult("LONG", 0, 5, 100,  90, "SL",  -10.0)
    assert win.is_win is True
    assert lose.is_win is False


def test_report_profit_factor_uses_gross_profit_over_gross_loss():
    from tools.backtest_replay import BacktestReport, TradeResult
    r = BacktestReport(bot="x", asset="A", bars_seen=100)
    r.trades = [
        TradeResult("LONG", 0, 5, 100, 110, "TP1",  10.0),
        TradeResult("LONG", 6, 10, 100, 120, "TP2", 20.0),
        TradeResult("LONG", 11, 15, 100, 95, "SL", -5.0),
    ]
    # gross_profit = 30, gross_loss = 5 → PF = 6.0
    assert r.profit_factor == pytest.approx(6.0)
    assert r.win_rate == pytest.approx(66.67, abs=0.1)


def test_report_max_drawdown_from_equity_curve():
    from tools.backtest_replay import BacktestReport, TradeResult
    r = BacktestReport(bot="x", asset="A", bars_seen=100)
    # +10, +20 (peak +30), -25 → dd = 25 from peak 30
    r.trades = [
        TradeResult("LONG", 0, 1, 100, 110, "TP",  10.0),
        TradeResult("LONG", 2, 3, 100, 120, "TP",  20.0),
        TradeResult("LONG", 4, 5, 100,  75, "SL", -25.0),
    ]
    assert r.max_drawdown_pct == pytest.approx(25.0)


def test_empty_report_returns_zeros():
    from tools.backtest_replay import BacktestReport
    r = BacktestReport(bot="x", asset="A", bars_seen=100)
    assert r.n_trades == 0
    assert r.win_rate == 0.0
    assert r.total_return_pct == 0.0


def test_replay_breakout_with_mocked_klines_returns_report():
    """With a flat synthetic kline series, no entries should fire."""
    from tools import backtest_replay
    # 50 flat bars with all indicator columns NaN-safe
    flat = pd.DataFrame({
        "open":   [100] * 50,
        "high":   [100] * 50,
        "low":    [100] * 50,
        "close":  [100] * 50,
        "volume": [1000] * 50,
    })
    with patch.object(backtest_replay, "_fetch_klines", return_value=flat):
        cfg = {
            "symbol": "BTCUSDT", "interval": "4h",
            "donchian_period": 20, "donchian_exit_period": 10,
            "atr_period": 14, "atr_sma_period": 20,
            "adx_period": 14, "adx_threshold": 20, "adx_exit_threshold": 15,
            "sl_atr_mult": 1.5, "allow_short": False,
        }
        report = backtest_replay.replay_breakout("BTC_4H", cfg, bars=50)
    assert report.bot == "breakout"
    assert report.asset == "BTC_4H"
    # Flat data → no Donchian breaks → no trades
    assert report.n_trades == 0
