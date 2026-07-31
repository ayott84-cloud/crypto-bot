"""Module 2 Phase S1 — wiring equities into the existing replay harness.

The harness is asset-class agnostic below one function: `_fetch_klines`
branches on `source` and everything downstream (BacktestReport,
check_intrabar_exit, the as-of higher-TF slicing, the cost deduction)
works on any market. This adds `source="equity"` and the equity cost
model — and pins that the crypto default is untouched.

Cost model note: 0.15% round-trip is a WEEX taker figure. US equities
are commission-free at Alpaca; the honest cost is spread + slippage +
sell-side regulatory fees, which is ~5bps on SPY-class ETFs and ~10bps
on mega-cap singles. Using the crypto constant would over-charge an ETF
strategy 3x and could fail a real edge.

Run: python -m pytest tests/test_equity_replay_seam.py -v
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

import market_calendar as mc
import tools.backtest_replay as br
from tools import _equity_bars as eb


# ─── Cost model ───────────────────────────────────────────────────────────

def test_crypto_default_cost_unchanged():
    assert br.DEFAULT_ROUND_TRIP_COST_PCT == 0.15


def test_equity_cost_defaults_by_instrument_class():
    assert br.default_cost_pct("crypto") == 0.15
    assert br.default_cost_pct("equity_etf") == pytest.approx(0.05)
    assert br.default_cost_pct("equity_single") == pytest.approx(0.10)


def test_equity_cost_resolves_from_symbol():
    """Liquid index ETFs get the ETF rate; anything else gets the more
    conservative single-name rate. Guessing cheap is the dangerous
    direction, so unknown symbols round UP."""
    assert br.cost_pct_for_symbol("SPY") == pytest.approx(0.05)
    assert br.cost_pct_for_symbol("QQQ") == pytest.approx(0.05)
    assert br.cost_pct_for_symbol("AAPL") == pytest.approx(0.10)
    assert br.cost_pct_for_symbol("SOMETHING_ILLIQUID") == pytest.approx(0.10)


# ─── _fetch_klines source branch ──────────────────────────────────────────

def _fake_equity_frame(n=30, end=date(2026, 7, 31)):
    days, d = [], end
    while len(days) < n:
        if mc.is_trading_day(d):
            days.append(d)
        d -= pd.Timedelta(days=1).to_pytimedelta()
    days.reverse()
    return eb.tiingo_rows_to_frame([{
        "date": d.isoformat(), "adjOpen": 100.0 + i, "adjHigh": 101.0 + i,
        "adjLow": 99.0 + i, "adjClose": 100.5 + i, "adjVolume": 1e6,
    } for i, d in enumerate(days)])


def test_fetch_klines_equity_source_returns_datetimeindex_frame(monkeypatch):
    frame = _fake_equity_frame(30)
    monkeypatch.setattr(eb, "fetch_daily",
                          lambda *a, **kw: frame)
    df = br._fetch_klines("SPY", "1d", 30, source="equity")
    assert isinstance(df.index, pd.DatetimeIndex), \
        "as-of slicing and resample REQUIRE a DateTimeIndex"
    assert len(df) == 30
    assert "close_time" in df.columns


def test_fetch_klines_equity_requests_enough_calendar_span(monkeypatch):
    """Asking for 252 BARS must request ~365 calendar days back, not 252
    — the 1.45x that mis-sizes every equity window."""
    seen = {}

    def spy(symbol, start, end, provider="tiingo", **kw):
        seen["span_days"] = (end - start).days
        return _fake_equity_frame(252)

    monkeypatch.setattr(eb, "fetch_daily", spy)
    br._fetch_klines("SPY", "1d", 252, source="equity")
    assert seen["span_days"] >= 365, \
        f"requested only {seen['span_days']} calendar days for 252 sessions"


def test_fetch_klines_equity_propagates_incompleteness(monkeypatch):
    """A frame the fetcher flagged as holed must stay flagged — a replay
    has to be able to refuse it."""
    frame = _fake_equity_frame(30)
    frame.attrs["incomplete"] = True
    monkeypatch.setattr(eb, "fetch_daily", lambda *a, **kw: frame)
    df = br._fetch_klines("SPY", "1d", 30, source="equity")
    assert df.attrs.get("incomplete") is True


def test_crypto_sources_still_route_unchanged(monkeypatch):
    called = {}

    def fake_chained(symbol, interval, count):
        called["binance"] = True
        return [[1_700_000_000_000 + i * 86_400_000, "1", "2", "0.5", "1.5",
                  "10", 1_700_000_000_000 + (i + 1) * 86_400_000 - 1,
                  "0", "0", "0", "0"] for i in range(count)]

    import tools._binance_klines as bk
    monkeypatch.setattr(bk, "fetch_klines_chained", fake_chained)
    df = br._fetch_klines("BTCUSDT", "1d", 10, source="binance")
    assert called.get("binance") is True
    assert len(df) == 10
