"""Jul 30 — the REAL root cause of the INJ_1H replay contradiction.

replay_breakout passed df_1d_full — the ENTIRE daily series ending at
run time — to analyze_breakout_entry for every historical bar. The
analyzer reads iloc[-1], so the trend gate evaluated TODAY'S 1D EMA
state against entries years in the past: a static gate, flipping the
whole replay's behavior with the forming daily bar (n=78 at 20:41,
n=101 at 22:00, same window). Scalp and crossover already slice
as-of per bar; breakout must too.

Second latent defect fixed alongside: the 1d fetch was capped at 365
days while a 17000-bar 1h window spans ~708 days — the older half of
the window had no gate data at all.

Run: python -m pytest tests/test_breakout_replay_asof_gate.py -v
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


def _base_df(days=10, freq="1h"):
    rows = days * 24
    idx = pd.date_range("2026-03-01", periods=rows, freq=freq)
    close = 100.0 + np.cumsum(np.random.default_rng(3).normal(0, 0.4, rows))
    return pd.DataFrame({
        "open": close, "high": close + 0.6, "low": close - 0.6,
        "close": close, "volume": 1000.0,
    }, index=idx)


def _daily_df(start="2026-02-01", days=60):
    idx = pd.date_range(start, periods=days, freq="1D")
    return pd.DataFrame({"close": 100.0 + np.arange(days) * 0.05}, index=idx)


_CFG = {"symbol": "TESTUSDT", "interval": "1h",
         "donchian_period": 20, "donchian_exit_period": 10,
         "atr_period": 14, "atr_sma_period": 20,
         "adx_period": 14, "adx_threshold": 20, "adx_exit_threshold": 15,
         "sl_atr_mult": 2.5, "use_trend_filter": True,
         "strategy_name": "TEST 1H Breakout"}


def test_trend_gate_is_evaluated_as_of_each_bar(monkeypatch):
    """The df_1d the analyzer sees must END at (or before) each bar's
    own day — never at the run day. With the static bug, every call
    saw the same final daily timestamp."""
    seen_last_1d = []
    real_analyze = None
    import breakout_signals

    def spy(window, cfg, df_1d=None):
        if df_1d is not None and len(df_1d) > 0:
            seen_last_1d.append((window.index[-1], df_1d.index[-1]))
        return {"would_enter": False, "blocked_by": "test",
                 "filters": {}, "values": {}, "direction": None}

    monkeypatch.setattr(br, "_fetch_klines",
                          lambda sym, iv, bars, source="weex": _daily_df())
    import tools.backtest_replay as _br
    monkeypatch.setattr(breakout_signals, "analyze_breakout_entry", spy)
    # replay imports the analyzer inside the function body — patch the
    # source module so the fresh import picks up the spy
    br.replay_breakout("TEST_1H", dict(_CFG), bars=240,
                         pre_fetched_df=_base_df(days=10))
    assert seen_last_1d, "spy never saw gate data"
    for bar_ts, last_1d_ts in seen_last_1d:
        assert last_1d_ts <= bar_ts + pd.Timedelta(days=1), (
            f"gate data from the future: bar {bar_ts} saw 1d {last_1d_ts}")
    distinct_days = {ts for _, ts in seen_last_1d}
    assert len(distinct_days) > 1, (
        "every bar saw the SAME final 1d row — static gate bug")


def test_1d_fetch_covers_the_full_base_window(monkeypatch):
    """A 17000-bar 1h window spans ~708 days; the 1d fetch must request
    enough days to cover it (was capped at 365)."""
    requested = {}

    def fake_fetch(sym, iv, bars, source="weex"):
        requested[iv] = bars
        return _daily_df(days=60)

    monkeypatch.setattr(br, "_fetch_klines", fake_fetch)
    br.replay_breakout("TEST_1H", dict(_CFG), bars=17000,
                         pre_fetched_df=_base_df(days=5))
    assert requested.get("1d", 0) >= 700, (
        f"1d fetch requested only {requested.get('1d')} days for a "
        "~708-day base window")
