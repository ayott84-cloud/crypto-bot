"""Jul 30 — the INJ_1H replay contradiction, root-caused.

replay_breakout fetches its 1D trend-gate series SEPARATELY from the
base window. When that fetch fails (rate-limited during concurrent
research runs), df_1d stays None and the trend filter silently degrades
to pass — the replay runs a DIFFERENT strategy (gate-off) and reports
it as live-parity. The Jul 30 CLI baseline/stress runs replayed
gate-off INJ_1H (n=101, PF 0.71/0.65) while the solo A/B replayed the
real strategy (n=78, PF 1.29). Same window, same config.

Rule (third instance of the class this month): a replay must never
silently degrade its own strategy — degraded gates get a warning on the
report AND in the output.

Run: python -m pytest tests/test_replay_gate_degrade_loud.py -v
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


def _base_df(rows=120):
    idx = pd.date_range("2026-01-01", periods=rows, freq="1h")
    close = 100.0 + np.cumsum(np.random.default_rng(7).normal(0, 0.5, rows))
    return pd.DataFrame({
        "open": close, "high": close + 0.5, "low": close - 0.5,
        "close": close, "volume": 1000.0,
    }, index=idx)


def _1d_df(rows):
    idx = pd.date_range("2025-06-01", periods=rows, freq="1D")
    return pd.DataFrame({"close": 100.0 + np.arange(rows) * 0.1}, index=idx)


_CFG = {"symbol": "TESTUSDT", "interval": "1h",
         "donchian_period": 55, "donchian_exit_period": 20,
         "atr_period": 14, "atr_sma_period": 20,
         "adx_period": 14, "adx_threshold": 20, "adx_exit_threshold": 15,
         "sl_atr_mult": 2.5, "use_trend_filter": True,
         "strategy_name": "TEST 1H Breakout"}


def _replay_with_1d(monkeypatch, fetched_1d):
    monkeypatch.setattr(br, "_fetch_klines",
                          lambda sym, iv, bars, source="weex": fetched_1d)
    return br.replay_breakout("TEST_1H", dict(_CFG), bars=120,
                                pre_fetched_df=_base_df())


def test_missing_1d_series_warns_gate_off(monkeypatch, capsys):
    rep = _replay_with_1d(monkeypatch, _1d_df(0))
    assert rep.warnings, "empty 1D series must be recorded on the report"
    assert "GATE-OFF" in rep.warnings[0]
    assert "GATE-OFF" in capsys.readouterr().out


def test_short_1d_series_warns_gate_off(monkeypatch):
    """<50 rows previously slipped through WITHOUT ema columns — the
    analyzer then passed silently. Must warn like the empty case."""
    rep = _replay_with_1d(monkeypatch, _1d_df(20))
    assert rep.warnings
    assert "GATE-OFF" in rep.warnings[0]


def test_fetch_exception_warns_gate_off(monkeypatch):
    def boom(sym, iv, bars, source="weex"):
        raise RuntimeError("rate limited")
    monkeypatch.setattr(br, "_fetch_klines", boom)
    rep = br.replay_breakout("TEST_1H", dict(_CFG), bars=120,
                               pre_fetched_df=_base_df())
    assert rep.warnings
    assert "GATE-OFF" in rep.warnings[0]


def test_healthy_1d_series_no_warnings(monkeypatch):
    rep = _replay_with_1d(monkeypatch, _1d_df(80))
    assert rep.warnings == []


def test_summary_line_carries_warning_marker(monkeypatch):
    rep = _replay_with_1d(monkeypatch, _1d_df(0))
    assert "⚠" in rep.summary_line()
    clean = _replay_with_1d(monkeypatch, _1d_df(80))
    assert "⚠" not in clean.summary_line()
