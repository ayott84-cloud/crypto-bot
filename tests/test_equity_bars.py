"""Module 2 Phase S1 — equity bar fetcher.

This module exists because the crypto fetcher's design cannot be reused.
tools/_binance_klines.py walks BACKWARD in date windows and terminates
on an empty chunk (`if not chunk: break`). Equity feeds return empty
chunks for every weekend and every holiday, so inheriting that line
would truncate every backtest at the first Saturday — the precise
failure class that corrupted a crypto validation round in July, and the
reason the SOL_4H verdict flipped once it was fixed.

The equity fetcher gets something the crypto one never could:
COMPLETENESS IS VERIFIABLE. market_calendar knows exactly how many
sessions belong in [start, end], so a short series is detectable rather
than merely suspected. A gap is reported loudly with the missing dates
attached; it is never silently accepted.

Run: python -m pytest tests/test_equity_bars.py -v
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
from tools import _equity_bars as eb


@pytest.fixture(autouse=True)
def _fake_credentials(monkeypatch):
    """Every fetch path fails fast without keys (by design — see
    test_missing_credentials_raise_clearly, which clears these again).
    Tests exercising transport/pagination need them present."""
    monkeypatch.setattr(eb, "TIINGO_API_KEY", "test-tiingo-token")
    monkeypatch.setattr(eb, "ALPACA_API_KEY", "test-alpaca-key")
    monkeypatch.setattr(eb, "ALPACA_API_SECRET", "test-alpaca-secret")


# ─── fixtures: fake provider payloads ─────────────────────────────────────

def _sessions(start: date, end: date):
    d, out = start, []
    while d <= end:
        if mc.is_trading_day(d):
            out.append(d)
        d += pd.Timedelta(days=1).to_pytimedelta()
    return out


def _tiingo_payload(days, base=100.0):
    return [{
        "date": f"{d.isoformat()}T00:00:00.000Z",
        "adjOpen": base + i, "adjHigh": base + i + 1.0,
        "adjLow": base + i - 1.0, "adjClose": base + i + 0.5,
        "adjVolume": 1_000_000 + i,
        "open": base + i, "high": base + i + 1.0,
        "low": base + i - 1.0, "close": base + i + 0.5,
        "volume": 1_000_000 + i,
    } for i, d in enumerate(days)]


def _alpaca_payload(days, base=100.0, token=None):
    return {
        "bars": [{
            "t": f"{d.isoformat()}T04:00:00Z",
            "o": base + i, "h": base + i + 1.0, "l": base + i - 1.0,
            "c": base + i + 0.5, "v": 1_000_000 + i,
        } for i, d in enumerate(days)],
        "next_page_token": token,
    }


# ─── Tiingo normalization ─────────────────────────────────────────────────

def test_tiingo_rows_to_frame_uses_adjusted_and_sorts():
    days = _sessions(date(2026, 8, 3), date(2026, 8, 7))     # Mon..Fri
    payload = list(reversed(_tiingo_payload(days)))          # unsorted input
    df = eb.tiingo_rows_to_frame(payload)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.is_monotonic_increasing
    assert len(df) == 5
    # adjusted close (base+i+0.5) must be used, not raw
    assert df["close"].iloc[0] == pytest.approx(100.5)


def test_tiingo_falls_back_to_raw_when_adjusted_absent():
    """Some Tiingo rows omit adj* fields; dropping those bars silently
    would punch holes in the series."""
    df = eb.tiingo_rows_to_frame([{
        "date": "2026-08-03T00:00:00.000Z",
        "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
        "volume": 500,
    }])
    assert len(df) == 1
    assert df["close"].iloc[0] == pytest.approx(10.5)


# ─── Alpaca normalization + cursor pagination ─────────────────────────────

def test_alpaca_pagination_follows_cursor_across_pages(monkeypatch):
    days = _sessions(date(2026, 8, 3), date(2026, 8, 14))
    page1, page2 = days[:5], days[5:]
    calls = []

    def fake_get(url, headers=None, params=None):
        calls.append(params.get("page_token"))
        if params.get("page_token") is None:
            return _alpaca_payload(page1, token="CURSOR1")
        return _alpaca_payload(page2, base=105.0, token=None)

    monkeypatch.setattr(eb, "_http_get_json", fake_get)
    df = eb.fetch_daily("SPY", date(2026, 8, 3), date(2026, 8, 14),
                          provider="alpaca", verify_complete=False)
    assert len(df) == len(days)
    assert calls == [None, "CURSOR1"], "cursor not followed exactly once"


def test_alpaca_stops_when_cursor_is_none(monkeypatch):
    days = _sessions(date(2026, 8, 3), date(2026, 8, 7))
    monkeypatch.setattr(eb, "_http_get_json",
                          lambda url, headers=None, params=None:
                          _alpaca_payload(days, token=None))
    df = eb.fetch_daily("SPY", date(2026, 8, 3), date(2026, 8, 7),
                          provider="alpaca", verify_complete=False)
    assert len(df) == 5


# ─── THE P0: weekend/holiday gaps must not terminate the series ──────────

def test_weekend_gap_does_not_truncate(monkeypatch):
    """Two full weeks spanning a weekend. The crypto fetcher's
    stop-on-empty-chunk would have ended this series on Friday."""
    days = _sessions(date(2026, 8, 3), date(2026, 8, 14))
    monkeypatch.setattr(eb, "_http_get_json",
                          lambda url, headers=None, params=None:
                          _tiingo_payload(days))
    df = eb.fetch_daily("SPY", date(2026, 8, 3), date(2026, 8, 14),
                          provider="tiingo")
    assert len(df) == 10, "series truncated at the weekend"
    assert df.index[0].date() == date(2026, 8, 3)
    assert df.index[-1].date() == date(2026, 8, 14)


def test_holiday_gap_does_not_truncate(monkeypatch):
    """Thanksgiving week 2026: Thu 11-26 closed, Fri 11-27 half day."""
    days = _sessions(date(2026, 11, 23), date(2026, 11, 30))
    monkeypatch.setattr(eb, "_http_get_json",
                          lambda url, headers=None, params=None:
                          _tiingo_payload(days))
    df = eb.fetch_daily("SPY", date(2026, 11, 23), date(2026, 11, 30),
                          provider="tiingo")
    assert date(2026, 11, 26) not in set(df.index.date)   # holiday absent
    assert date(2026, 11, 27) in set(df.index.date)       # half day present
    assert date(2026, 11, 30) in set(df.index.date)       # survived the gap


# ─── Calendar-verified completeness (loud, never silent) ─────────────────

def test_missing_sessions_are_reported_loudly(monkeypatch, capsys):
    full = _sessions(date(2026, 8, 3), date(2026, 8, 14))
    holed = full[:4] + full[7:]                     # drop 3 mid-series days
    monkeypatch.setattr(eb, "_http_get_json",
                          lambda url, headers=None, params=None:
                          _tiingo_payload(holed))
    rep = eb.completeness_report("SPY", eb.tiingo_rows_to_frame(
        _tiingo_payload(holed)), date(2026, 8, 3), date(2026, 8, 14))
    assert rep["complete"] is False
    assert rep["expected"] == len(full)
    assert rep["got"] == len(holed)
    assert len(rep["missing"]) == 3
    assert full[4] in rep["missing"]

    df = eb.fetch_daily("SPY", date(2026, 8, 3), date(2026, 8, 14),
                          provider="tiingo")
    out = capsys.readouterr().out
    assert "INCOMPLETE" in out and "SPY" in out
    assert getattr(df, "attrs", {}).get("incomplete") is True


def test_complete_series_is_silent(monkeypatch, capsys):
    days = _sessions(date(2026, 8, 3), date(2026, 8, 14))
    monkeypatch.setattr(eb, "_http_get_json",
                          lambda url, headers=None, params=None:
                          _tiingo_payload(days))
    df = eb.fetch_daily("SPY", date(2026, 8, 3), date(2026, 8, 14),
                          provider="tiingo")
    assert "INCOMPLETE" not in capsys.readouterr().out
    assert df.attrs.get("incomplete") is False


def test_completeness_ignores_leading_prelisting_absence():
    """A symbol that listed mid-window is not 'incomplete' before its
    first bar — only INTERIOR holes count as defects."""
    full = _sessions(date(2026, 8, 3), date(2026, 8, 14))
    late = full[5:]
    rep = eb.completeness_report("NEWCO", eb.tiingo_rows_to_frame(
        _tiingo_payload(late)), date(2026, 8, 3), date(2026, 8, 14))
    assert rep["complete"] is True
    assert rep["leading_absent"] == 5


# ─── Transport hardening ──────────────────────────────────────────────────

def test_transient_error_retries_then_succeeds(monkeypatch):
    days = _sessions(date(2026, 8, 3), date(2026, 8, 7))
    calls = {"n": 0}

    def flaky(url, headers=None, params=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise eb.TransientFetchError("SSL EOF")
        return _tiingo_payload(days)

    monkeypatch.setattr(eb, "_http_get_json_once", flaky)
    monkeypatch.setattr(eb.time, "sleep", lambda s: None)
    rows = eb._http_get_json("https://x", headers={}, params={})
    assert calls["n"] == 3 and len(rows) == 5


def test_exhausted_retries_raise_not_return_empty(monkeypatch):
    """An empty return would be read as 'no data for this window' and
    silently shrink a backtest. It must raise."""
    def always(url, headers=None, params=None):
        raise eb.TransientFetchError("down")
    monkeypatch.setattr(eb, "_http_get_json_once", always)
    monkeypatch.setattr(eb.time, "sleep", lambda s: None)
    with pytest.raises(eb.TransientFetchError):
        eb._http_get_json("https://x", headers={}, params={})


def test_missing_credentials_raise_clearly(monkeypatch):
    """Clear BOTH the module constant and the environment: _load_env()
    calls load_dotenv, which POPULATES os.environ, so a real .env on the
    box would otherwise satisfy the accessor's fallback and this test
    would pass or fail depending on the machine."""
    monkeypatch.setattr(eb, "TIINGO_API_KEY", "")
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    with pytest.raises(eb.CredentialsMissing):
        eb.fetch_daily("SPY", date(2026, 8, 3), date(2026, 8, 7),
                        provider="tiingo")


# ─── Interop with the existing replay harness ────────────────────────────

def test_positional_rows_match_the_weex_11_column_shape():
    days = _sessions(date(2026, 8, 3), date(2026, 8, 7))
    df = eb.tiingo_rows_to_frame(_tiingo_payload(days))
    rows = eb.to_positional_rows(df, interval="1d")
    assert all(len(r) == 11 for r in rows)
    from signals import build_dataframe
    rebuilt = build_dataframe(rows)
    assert "close_time" in rebuilt.columns
    assert len(rebuilt) == len(days)
    assert float(rebuilt["close"].iloc[0]) == pytest.approx(
        float(df["close"].iloc[0]))


def test_close_time_is_the_session_close_not_midnight():
    """Daily equity bars stamped at midnight would put the bar's close
    17 hours before the market actually closed, breaking any as-of
    higher-timeframe slice."""
    df = eb.tiingo_rows_to_frame(_tiingo_payload(
        _sessions(date(2026, 8, 3), date(2026, 8, 3))))
    rows = eb.to_positional_rows(df, interval="1d")
    close_ms = rows[0][6]
    ts = pd.Timestamp(close_ms, unit="ms", tz="UTC").tz_convert(
        "America/New_York")
    assert (ts.hour, ts.minute) == (16, 0)


def test_close_time_respects_half_day():
    df = eb.tiingo_rows_to_frame(_tiingo_payload(
        _sessions(date(2026, 11, 27), date(2026, 11, 27))))
    rows = eb.to_positional_rows(df, interval="1d")
    ts = pd.Timestamp(rows[0][6], unit="ms", tz="UTC").tz_convert(
        "America/New_York")
    assert ts.hour == 13


# ─── Cross-vendor validation (adjustment errors are the #1 silent killer) ─

def test_cross_check_flags_adjustment_divergence():
    days = _sessions(date(2026, 8, 3), date(2026, 8, 14))
    a = eb.tiingo_rows_to_frame(_tiingo_payload(days))
    b = a.copy()
    b.loc[b.index[3], "close"] *= 1.5            # unadjusted split
    rep = eb.cross_check(a, b, tol_pct=0.5)
    assert rep["agree"] is False
    assert rep["max_divergence_pct"] > 40
    assert rep["worst_date"] == b.index[3].date()


def test_cross_check_passes_on_matching_series():
    days = _sessions(date(2026, 8, 3), date(2026, 8, 14))
    a = eb.tiingo_rows_to_frame(_tiingo_payload(days))
    b = a.copy()
    b["close"] *= 1.0001                          # rounding noise
    rep = eb.cross_check(a, b, tol_pct=0.5)
    assert rep["agree"] is True


def test_cross_check_compares_only_shared_dates():
    """A shorter vendor history must not fail the check — only the
    overlap is comparable. (Slice the SAME payload so prices agree;
    rebuilding it would restart the price ramp and manufacture a
    divergence that isn't there.)"""
    days = _sessions(date(2026, 8, 3), date(2026, 8, 14))
    payload = _tiingo_payload(days)
    a = eb.tiingo_rows_to_frame(payload)
    b = eb.tiingo_rows_to_frame(payload[3:])
    rep = eb.cross_check(a, b, tol_pct=0.5)
    assert rep["agree"] is True
    assert rep["compared"] == len(days) - 3
