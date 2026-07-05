"""4h synthesis for the Coinbase-backed long-window fetcher.

Coinbase's candles API supports only 60/300/900/3600/21600/86400s
granularities — no 4h. Breakout's 4h assets need long windows, so
fetch_klines_chained aggregates 1h rows into 4h buckets.

Run: python -m pytest tests/test_binance_klines_aggregate.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

H1_MS = 3_600_000
H4_MS = 4 * H1_MS


def _mk_1h_rows(start_ms, n, base=100.0):
    """Synthetic chronological 1h rows in WEEX positional shape."""
    rows = []
    for i in range(n):
        o = base + i
        rows.append([
            start_ms + i * H1_MS,
            str(o), str(o + 2.0), str(o - 1.0), str(o + 1.0), str(10.0),
            start_ms + (i + 1) * H1_MS - 1,
            "0", "0", "0", "0",
        ])
    return rows


def test_aggregate_4h_ohlcv_semantics():
    from tools._binance_klines import _aggregate_rows
    # 8 aligned 1h bars starting exactly on a 4h boundary → 2 full buckets
    start = 1_750_000_000_000 // H4_MS * H4_MS
    rows = _mk_1h_rows(start, 8)
    out = _aggregate_rows(rows, H4_MS)
    assert len(out) == 2
    b0 = out[0]
    assert int(b0[0]) == start                      # bucket open time
    assert float(b0[1]) == 100.0                    # open = first bar's open
    assert float(b0[2]) == 105.0                    # high = max(high) = 103+2
    assert float(b0[3]) == 99.0                     # low = min(low)
    assert float(b0[4]) == 104.0                    # close = last bar's close (103+1)
    assert float(b0[5]) == pytest.approx(40.0)      # volume summed


def test_aggregate_drops_partial_leading_bucket():
    from tools._binance_klines import _aggregate_rows
    start = 1_750_000_000_000 // H4_MS * H4_MS
    # first 2 bars land mid-bucket (pagination cutoff), then 4 aligned
    rows = _mk_1h_rows(start + 2 * H1_MS, 6)
    out = _aggregate_rows(rows, H4_MS)
    # partial first bucket dropped; one complete bucket remains
    assert len(out) == 1
    assert int(out[0][0]) == start + H4_MS


def test_breakout_long_window_symbols_mapped():
    from tools._binance_klines import _weex_to_coinbase
    for sym in ("NEARUSDT", "AAVEUSDT", "INJUSDT"):
        assert _weex_to_coinbase(sym) is not None, sym
    # BNB/TRX are genuinely unlisted on Coinbase — must stay None (skip)
    assert _weex_to_coinbase("BNBUSDT") is None
    assert _weex_to_coinbase("TRXUSDT") is None


def test_momentum_universe_symbols_mapped():
    """The Jul 4 momentum long-window run showed 0 trades for these —
    missing MAPPINGS, not missing signals. All are Coinbase-listed."""
    from tools._binance_klines import _weex_to_coinbase
    for sym in ("DOTUSDT", "LTCUSDT", "UNIUSDT", "FILUSDT", "ETCUSDT",
                  "APTUSDT", "ARBUSDT", "ATOMUSDT", "SUIUSDT", "HBARUSDT",
                  "OPUSDT", "RENDERUSDT", "SHIBUSDT", "ICPUSDT"):
        assert _weex_to_coinbase(sym) is not None, sym


def test_chained_fetch_survives_short_chunks(monkeypatch):
    """Coinbase routinely returns 299-bar chunks (boundary rounding).
    The chain must keep paginating while chunks are NON-EMPTY — only an
    empty chunk means history is exhausted. The old `len < limit` break
    silently truncated a 4500-bar request to ~375 bars (P4 Step 1)."""
    import tools._binance_klines as bk

    now_ms = 1_750_000_000_000 // H1_MS * H1_MS

    def fake_one_call(symbol, interval, end_time_ms, limit):
        end = now_ms if end_time_ms is None else (int(end_time_ms) + 1) // H1_MS * H1_MS
        # serve 299 bars per call (one short of the 300 cap), endless history
        n = min(299, limit)
        start = end - n * H1_MS
        return _mk_1h_rows(start, n)

    monkeypatch.setattr(bk, "_one_call", fake_one_call)
    monkeypatch.setattr(bk, "_RATE_LIMIT_SLEEP_S", 0)
    rows = bk.fetch_klines_chained("BTCUSDT", "1h", 1200)
    assert len(rows) == 1200
    # chronological + contiguous
    times = [int(r[0]) for r in rows]
    assert times == sorted(times)
    assert all(b - a == H1_MS for a, b in zip(times, times[1:]))


def test_chained_fetch_stops_on_empty_chunk(monkeypatch):
    """History exhaustion (empty chunk) must still terminate the chain."""
    import tools._binance_klines as bk

    now_ms = 1_750_000_000_000 // H1_MS * H1_MS
    listing_ms = now_ms - 500 * H1_MS   # only 500 bars of history exist

    def fake_one_call(symbol, interval, end_time_ms, limit):
        end = now_ms if end_time_ms is None else (int(end_time_ms) + 1) // H1_MS * H1_MS
        start = max(listing_ms, end - min(299, limit) * H1_MS)
        if start >= end:
            return []
        return _mk_1h_rows(start, (end - start) // H1_MS)

    monkeypatch.setattr(bk, "_one_call", fake_one_call)
    monkeypatch.setattr(bk, "_RATE_LIMIT_SLEEP_S", 0)
    rows = bk.fetch_klines_chained("BTCUSDT", "1h", 5000)
    assert len(rows) == 500   # everything that exists, no infinite loop
