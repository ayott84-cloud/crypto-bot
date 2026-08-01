"""Module 2 — the bar cache must not require pyarrow.

Found on the first successful droplet fetch: the data came back fine and
the run died in save_cache because neither pyarrow nor fastparquet is
installed. Adding pyarrow (~100MB) to a 1GB box for a convenience cache
is the wrong trade, so the cache degrades to gzipped CSV — same data,
smaller container, zero new dependencies.

Two properties matter beyond "it round-trips":
  * The DatetimeIndex must survive. CSV has no types; a string index
    would break every as-of slice and resample downstream.
  * The COMPLETENESS VERDICT must survive. df.attrs does not persist
    through any serializer, so a cached frame would silently lose the
    "this window has holes" flag — turning a loud defect back into a
    silent one, which is the failure class this module exists to kill.
    Rather than store it, we RECOMPUTE it from the calendar on load:
    cheap, and it cannot go stale.

Run: python -m pytest tests/test_equity_cache_fallback.py -v
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


def _sessions(start, end):
    d, out = start, []
    while d <= end:
        if mc.is_trading_day(d):
            out.append(d)
        d += pd.Timedelta(days=1).to_pytimedelta()
    return out


def _frame(days):
    return eb.tiingo_rows_to_frame([{
        "date": d.isoformat(), "adjOpen": 100.0 + i, "adjHigh": 101.0 + i,
        "adjLow": 99.0 + i, "adjClose": 100.5 + i, "adjVolume": 1e6,
    } for i, d in enumerate(days)])


@pytest.fixture(autouse=True)
def _tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(eb, "CACHE_DIR", tmp_path / "equity")


def test_cache_round_trips_without_pyarrow(monkeypatch):
    """Simulate the droplet: no parquet engine available."""
    def no_parquet(*a, **kw):
        raise ImportError("pyarrow is required for parquet support")
    df = _frame(_sessions(date(2026, 8, 3), date(2026, 8, 14)))
    monkeypatch.setattr(pd.DataFrame, "to_parquet", no_parquet, raising=False)

    p = eb.save_cache(df, "SPY", "1d", "tiingo")
    assert p.exists()
    assert p.suffix == ".gz", f"expected the csv.gz fallback, got {p.name}"

    back = eb.load_cache("SPY", "1d", "tiingo")
    assert back is not None
    assert len(back) == len(df)
    pd.testing.assert_frame_equal(back, df, check_freq=False)


def test_cached_index_is_still_a_datetimeindex(monkeypatch):
    """A string index would silently break resample and as-of slicing —
    the exact defect that no-op'd the higher-TF filter in June."""
    def no_parquet(*a, **kw):
        raise ImportError("no engine")
    monkeypatch.setattr(pd.DataFrame, "to_parquet", no_parquet, raising=False)
    df = _frame(_sessions(date(2026, 8, 3), date(2026, 8, 14)))
    eb.save_cache(df, "QQQ", "1d", "tiingo")
    back = eb.load_cache("QQQ", "1d", "tiingo")
    assert isinstance(back.index, pd.DatetimeIndex)
    assert back.index.is_monotonic_increasing


def test_completeness_verdict_is_restored_on_load(monkeypatch):
    """attrs never survive serialization, so a holed window would come
    back from cache looking clean. Recompute from the calendar."""
    def no_parquet(*a, **kw):
        raise ImportError("no engine")
    monkeypatch.setattr(pd.DataFrame, "to_parquet", no_parquet, raising=False)

    full = _sessions(date(2026, 8, 3), date(2026, 8, 14))
    holed = _frame(full[:4] + full[7:])          # 3 interior sessions missing
    eb.save_cache(holed, "IWM", "1d", "tiingo")
    back = eb.load_cache("IWM", "1d", "tiingo")
    assert back.attrs.get("incomplete") is True
    assert len(back.attrs["completeness"]["missing"]) == 3


def test_clean_cached_window_is_not_flagged(monkeypatch):
    def no_parquet(*a, **kw):
        raise ImportError("no engine")
    monkeypatch.setattr(pd.DataFrame, "to_parquet", no_parquet, raising=False)
    df = _frame(_sessions(date(2026, 8, 3), date(2026, 8, 14)))
    eb.save_cache(df, "EFA", "1d", "tiingo")
    back = eb.load_cache("EFA", "1d", "tiingo")
    assert back.attrs.get("incomplete") is False


def test_load_cache_missing_symbol_returns_none():
    assert eb.load_cache("NOPE", "1d", "tiingo") is None


def test_parquet_is_used_when_an_engine_exists():
    """Where pyarrow IS available (dev boxes), keep the typed format."""
    pytest.importorskip("pyarrow")
    df = _frame(_sessions(date(2026, 8, 3), date(2026, 8, 14)))
    p = eb.save_cache(df, "VNQ", "1d", "tiingo")
    assert p.suffix == ".parquet"
    back = eb.load_cache("VNQ", "1d", "tiingo")
    assert back is not None and len(back) == len(df)
