"""Aug 14 2026 — the live equity fetch diverged from the validated one.

Module 2 was unpaused Aug 2 and never evaluated a single entry: both
reversion sleeves reported blocked_by="insufficient_history" every
cycle. analyze_reversion_entry needs sma_period + 2 = 202 daily bars.

stock_executor.get_klines asked Alpaca for bars with `limit` and NO
`start`/`end` and no cursor pagination, so it came back short. The
validated fetcher (tools/_equity_bars._fetch_alpaca_daily) has done a
ranged, paginated request since S1 — the live daemon just never used it.

Fourth instance of the same shape this session: the replay's trailing
knob live never had, the static-gate replay divergence, the whale funnel
wired to a function the daemon does not call, and now this. The fix is
delegation, not a parallel patch — a second fetcher is the defect.

Run: python -m pytest tests/test_stock_executor_bars.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

import stock_executor as se
import stock_signals as ss


def _frame(n: int, start="2024-01-02"):
    idx = pd.bdate_range(start=start, periods=n, tz=None)
    base = pd.Series(range(n), dtype=float) + 100.0
    return pd.DataFrame(
        {"open": base.values, "high": base.values + 1.0,
         "low": base.values - 1.0, "close": base.values,
         "volume": [1e6] * n}, index=idx)


# ─── The request must be ranged, not a bare limit ────────────────────────

def test_get_klines_delegates_to_the_validated_fetcher(monkeypatch):
    seen = {}

    def fake_fetch(symbol, start, end, provider="tiingo", verify_complete=True):
        seen.update(symbol=symbol, start=start, end=end, provider=provider)
        return _frame(300)

    monkeypatch.setattr(se, "_fetch_daily", fake_fetch)
    rows = se.StockExecutor.get_klines(
        se.StockExecutor.__new__(se.StockExecutor), "SPY", "1d", 300)
    assert seen["symbol"] == "SPY"
    assert seen["start"] is not None and seen["end"] is not None, \
        "a bare limit with no date range is what broke Module 2"
    assert len(rows) == 300


def test_requested_bar_count_maps_to_a_wide_enough_calendar_span(monkeypatch):
    """252 trading days per year — asking for 400 bars over a 400 CALENDAR
    day window would come up ~120 sessions short."""
    seen = {}

    def fake_fetch(symbol, start, end, provider="tiingo", verify_complete=True):
        seen.update(start=start, end=end)
        return _frame(400)

    monkeypatch.setattr(se, "_fetch_daily", fake_fetch)
    se.StockExecutor.get_klines(
        se.StockExecutor.__new__(se.StockExecutor), "SPY", "1d", 400)
    span_days = (seen["end"] - seen["start"]).days
    assert span_days >= 400 * 365 / 252, \
        f"span {span_days}d cannot contain 400 trading sessions"


def test_rows_keep_the_11_column_positional_shape(monkeypatch):
    monkeypatch.setattr(se, "_fetch_daily",
                          lambda *a, **k: _frame(210))
    rows = se.StockExecutor.get_klines(
        se.StockExecutor.__new__(se.StockExecutor), "SPY", "1d", 210)
    assert all(len(r) == 11 for r in rows)
    assert rows == sorted(rows, key=lambda r: r[0]), "rows must be chronological"


def test_a_short_series_is_announced_not_swallowed(monkeypatch, caplog):
    """Returning 40 bars when 400 were asked for is the exact silence
    that hid this bug for 12 days."""
    monkeypatch.setattr(se, "_fetch_daily", lambda *a, **k: _frame(40))
    with caplog.at_level("WARNING"):
        rows = se.StockExecutor.get_klines(
            se.StockExecutor.__new__(se.StockExecutor), "SPY", "1d", 400)
    assert len(rows) == 40
    assert any("SHORT" in r.message.upper() or "40" in r.message
                for r in caplog.records), "a short series returned silently"


def test_fetch_failure_returns_empty_rather_than_raising(monkeypatch):
    """The daemon treats [] as 'skip this symbol this cycle'. An
    exception would take the whole cycle down with it."""
    def boom(*a, **k):
        raise RuntimeError("alpaca 500")
    monkeypatch.setattr(se, "_fetch_daily", boom)
    assert se.StockExecutor.get_klines(
        se.StockExecutor.__new__(se.StockExecutor), "SPY", "1d", 400) == []


def test_intraday_is_refused_rather_than_silently_short(monkeypatch):
    """S5 has not built an intraday path. Returning a short series would
    look like data; refusing says what is actually true."""
    with pytest.raises(NotImplementedError):
        se.StockExecutor.get_klines(
            se.StockExecutor.__new__(se.StockExecutor), "SPY", "5m", 400)


# ─── The end-to-end property that actually failed ────────────────────────

def test_a_full_fetch_clears_the_reversion_history_gate(monkeypatch):
    """The regression: 202+ bars must reach analyze_reversion_entry so
    blocked_by stops being 'insufficient_history'."""
    monkeypatch.setattr(se, "_fetch_daily", lambda *a, **k: _frame(300))
    rows = se.StockExecutor.get_klines(
        se.StockExecutor.__new__(se.StockExecutor), "SPY", "1d", 400)
    from signals import build_dataframe
    df = build_dataframe(rows).dropna(subset=["close"])
    sig = ss.analyze_reversion_entry(df, {"sma_period": 200})
    assert sig["blocked_by"] != "insufficient_history"
