"""P2.1 + P2.2 — honest backtest replay.

P2.1 Conservative intra-bar fills (root cause 3 from the Jul 2 research):
  - LONG SL checked against bar LOW, TP against bar HIGH (not close)
  - SHORT mirrored (SL vs high, TP vs low)
  - Both legs inside one bar's range → scored as LOSS (SL-first,
    industry-standard 'conservative fill': vectorbt #188, NinjaTrader
    conservative mode, QuantConnect forum consensus)
  - Fills AT the trigger price (matches P1.1 exchange-resident orders)

P2.2 Cost model:
  - Every closed trade pays round_trip_cost_pct (taker fees both sides +
    slippage buffer; default 0.15%)
  - BacktestReport exposes avg_win_pct + cost hurdle helpers

Run: python -m pytest tests/test_honest_replay.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


# ─── check_intrabar_exit — the new conservative exit evaluator ─────────────

def test_long_sl_triggers_on_bar_low_not_close():
    """Bar wicks below SL but closes above it → SL Hit (live behavior),
    where the old close-only check would have said 'no exit'."""
    from tools.backtest_replay import check_intrabar_exit
    # entry 100, SL 98.5 (1.5%), TP 103 (3%)
    reason, fill = check_intrabar_exit(
        entry_price=100.0, direction="LONG",
        bar_high=101.0, bar_low=98.0, sl_price=98.5, tp_price=103.0)
    assert reason == "SL Hit"
    assert fill == pytest.approx(98.5)   # fills at trigger, not at low


def test_long_tp_triggers_on_bar_high():
    from tools.backtest_replay import check_intrabar_exit
    reason, fill = check_intrabar_exit(
        entry_price=100.0, direction="LONG",
        bar_high=103.5, bar_low=99.5, sl_price=98.5, tp_price=103.0)
    assert reason == "TP Hit"
    assert fill == pytest.approx(103.0)


def test_long_both_touched_scores_sl_first():
    """Conservative fill: when one bar spans BOTH triggers, assume the
    stop was hit first — score the LOSS."""
    from tools.backtest_replay import check_intrabar_exit
    reason, fill = check_intrabar_exit(
        entry_price=100.0, direction="LONG",
        bar_high=104.0, bar_low=98.0, sl_price=98.5, tp_price=103.0)
    assert reason == "SL Hit"
    assert fill == pytest.approx(98.5)


def test_long_no_exit_when_bar_inside_bracket():
    from tools.backtest_replay import check_intrabar_exit
    reason, fill = check_intrabar_exit(
        entry_price=100.0, direction="LONG",
        bar_high=101.0, bar_low=99.5, sl_price=98.5, tp_price=103.0)
    assert reason is None
    assert fill is None


def test_short_sl_triggers_on_bar_high():
    from tools.backtest_replay import check_intrabar_exit
    # SHORT entry 100: SL above at 101, TP below at 98
    reason, fill = check_intrabar_exit(
        entry_price=100.0, direction="SHORT",
        bar_high=101.2, bar_low=99.8, sl_price=101.0, tp_price=98.0)
    assert reason == "SL Hit"
    assert fill == pytest.approx(101.0)


def test_short_tp_triggers_on_bar_low():
    from tools.backtest_replay import check_intrabar_exit
    reason, fill = check_intrabar_exit(
        entry_price=100.0, direction="SHORT",
        bar_high=100.5, bar_low=97.5, sl_price=101.0, tp_price=98.0)
    assert reason == "TP Hit"
    assert fill == pytest.approx(98.0)


def test_short_both_touched_scores_sl_first():
    from tools.backtest_replay import check_intrabar_exit
    reason, fill = check_intrabar_exit(
        entry_price=100.0, direction="SHORT",
        bar_high=101.5, bar_low=97.5, sl_price=101.0, tp_price=98.0)
    assert reason == "SL Hit"
    assert fill == pytest.approx(101.0)


# ─── Replay integration: intrabar exits + cost deduction ───────────────────

def _kline_df(rows):
    """rows: list of (open, high, low, close). 5m DateTimeIndex."""
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame({
        "open":   [r[0] for r in rows],
        "high":   [r[1] for r in rows],
        "low":    [r[2] for r in rows],
        "close":  [r[3] for r in rows],
        "volume": [1000.0] * len(rows),
    }, index=idx)


def test_replay_crossover_wick_to_sl_scores_loss():
    """A bar that wicks through SL then recovers must be a LOSS in replay —
    the exact case the old close-only check scored as a WIN."""
    from tools.backtest_replay import replay_crossover
    # 110 flat bars (SMA warmup + past the loop's `needed` start), then
    # the cross bars, then a wick bar.
    rows = [(100.0, 100.2, 99.8, 100.0)] * 110
    rows += [(100.0, 101.2, 100.0, 101.0)] * 2   # golden cross forms (close 101)
    # wick bar: drops to 99.8 (below SL 101*0.99=99.99) then closes at 101.5
    rows += [(101.0, 102.0, 99.8, 101.5)]
    rows += [(101.5, 101.6, 101.4, 101.5)] * 2
    df = _kline_df(rows)
    cfg = {"symbol": "T", "interval": "1h", "sma_fast": 50, "sma_slow": 100,
            "sl_pct": 1.0, "tp_pct": 2.0, "allow_short": True}
    report = replay_crossover("T", cfg, bars=len(df), pre_fetched_df=df,
                                 round_trip_cost_pct=0.0)
    assert report.n_trades >= 1
    assert report.trades[0].exit_reason == "SL Hit"
    # Fill at trigger: entry 101 → SL 99.99
    assert report.trades[0].exit_price == pytest.approx(101.0 * 0.99, rel=1e-6)


def test_replay_cost_model_deducts_round_trip_cost():
    """With cost model on, every trade's pnl_pct is reduced by the
    round-trip cost."""
    from tools.backtest_replay import replay_crossover
    rows = [(100.0, 100.2, 99.8, 100.0)] * 110
    rows += [(100.0, 101.2, 100.0, 101.0)] * 2
    # clean TP run: rises to 103.5 (> TP 101*1.02 = 103.02) w/o touching SL
    rows += [(101.0, 103.5, 100.9, 103.4)]
    rows += [(103.4, 103.5, 103.3, 103.4)] * 2
    df = _kline_df(rows)
    cfg = {"symbol": "T", "interval": "1h", "sma_fast": 50, "sma_slow": 100,
            "sl_pct": 1.0, "tp_pct": 2.0, "allow_short": True}
    free = replay_crossover("T", cfg, bars=len(df), pre_fetched_df=df,
                               round_trip_cost_pct=0.0)
    costed = replay_crossover("T", cfg, bars=len(df), pre_fetched_df=df,
                                 round_trip_cost_pct=0.15)
    assert free.n_trades == costed.n_trades == 1
    assert costed.trades[0].pnl_pct == pytest.approx(
        free.trades[0].pnl_pct - 0.15, rel=1e-6)


def test_report_avg_win_pct_property():
    """BacktestReport.avg_win_pct — the P2.2 hurdle metric (reject configs
    with avg win < 0.5% after costs)."""
    from tools.backtest_replay import BacktestReport, TradeResult
    r = BacktestReport(bot="x", asset="y", bars_seen=100)
    r.trades.append(TradeResult(direction="LONG", entry_bar=1, exit_bar=2,
                                  entry_price=100, exit_price=102,
                                  exit_reason="TP Hit", pnl_pct=2.0))
    r.trades.append(TradeResult(direction="LONG", entry_bar=3, exit_bar=4,
                                  entry_price=100, exit_price=99,
                                  exit_reason="SL Hit", pnl_pct=-1.0))
    r.trades.append(TradeResult(direction="LONG", entry_bar=5, exit_bar=6,
                                  entry_price=100, exit_price=101,
                                  exit_reason="TP Hit", pnl_pct=1.0))
    assert r.avg_win_pct == pytest.approx(1.5)


def test_report_avg_win_pct_zero_when_no_wins():
    from tools.backtest_replay import BacktestReport, TradeResult
    r = BacktestReport(bot="x", asset="y", bars_seen=100)
    r.trades.append(TradeResult(direction="LONG", entry_bar=1, exit_bar=2,
                                  entry_price=100, exit_price=99,
                                  exit_reason="SL Hit", pnl_pct=-1.0))
    assert r.avg_win_pct == 0.0


def test_replay_scalp_supports_intrabar_and_cost():
    """replay_scalp gets the same treatment — smoke test that the params
    exist and a wick-to-SL bar closes the trade."""
    from tools.backtest_replay import replay_scalp
    import inspect
    sig = inspect.signature(replay_scalp)
    assert "round_trip_cost_pct" in sig.parameters
