"""Tests for the Binance backtest data fetcher (tools/_binance_klines.py).

These are pure-Python tests — the network is mocked. Live HTTP behavior
is verified on the droplet at deploy time (the Windows dev box has SSL
cert verification quirks that don't reflect production).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest


def test_interval_ms_known_values():
    from tools._binance_klines import _interval_ms
    assert _interval_ms("5m")  == 300_000
    assert _interval_ms("1h")  == 3_600_000
    assert _interval_ms("4h")  == 14_400_000
    assert _interval_ms("1d")  == 86_400_000
    assert _interval_ms("1w")  == 604_800_000


def test_interval_ms_unknown_raises():
    from tools._binance_klines import _interval_ms
    with pytest.raises(ValueError):
        _interval_ms("xyz")


def test_fetch_chained_returns_chronological_when_chunks_arrive_reverse():
    """The chained fetcher walks backward in time (end decreasing).
    First call returns the MOST RECENT 1000 bars; second call walks
    earlier. Final accumulated array must be chronological (oldest first).

    Bybit V5 caps each call at 1000 bars (note: _one_call mocked here
    is the post-reversal chronological-within-chunk shape — the actual
    HTTP-level reversal is handled inside _one_call)."""
    from tools._binance_klines import fetch_klines_chained
    # First call (no end → most recent): bars 1000-1999
    chunk_recent = [[t * 300_000, "1", "2", "0.5", "1.5", "100"]
                     for t in range(1000, 2000)]
    # Second call (end walks backward): bars 0-999
    chunk_older  = [[t * 300_000, "1", "2", "0.5", "1.5", "100"]
                     for t in range(0, 1000)]

    def _mock_one_call(symbol, interval, end_time_ms, limit):
        if end_time_ms is None:
            return chunk_recent
        return chunk_older

    with patch("tools._binance_klines._one_call", side_effect=_mock_one_call):
        rows = fetch_klines_chained("BTCUSDT", "5m", 2000)
    assert len(rows) == 2000
    # First row is oldest (open_time = 0); last row is newest
    assert rows[0][0]  == 0
    assert rows[-1][0] == 1999 * 300_000


def test_fetch_chained_returns_empty_on_first_failure():
    from tools._binance_klines import fetch_klines_chained
    with patch("tools._binance_klines._one_call", return_value=[]):
        rows = fetch_klines_chained("BTCUSDT", "5m", 1000)
    assert rows == []


def test_fetch_chained_invalid_interval_raises():
    from tools._binance_klines import fetch_klines_chained
    with pytest.raises(ValueError):
        fetch_klines_chained("BTCUSDT", "bogus", 1000)


def test_fetch_chained_handles_partial_history():
    """If Bybit returns fewer bars than asked (symbol listed mid-window),
    the helper stops chaining rather than infinite-looping."""
    from tools._binance_klines import fetch_klines_chained
    # First call returns only 800 bars (less than the 1000 asked for)
    partial = [[t * 300_000, "1", "2", "0.5", "1.5", "100"] for t in range(800)]
    with patch("tools._binance_klines._one_call", return_value=partial):
        rows = fetch_klines_chained("OBSCUREUSDT", "5m", 5000)
    assert len(rows) == 800  # got what was available, stopped


def test_fetch_chained_trims_to_requested_size():
    """When the last chunk overshoots the requested total, the result
    is trimmed to the exact requested size."""
    from tools._binance_klines import fetch_klines_chained
    full_chunk = [[t * 300_000, "1", "2", "0.5", "1.5", "100"]
                   for t in range(1000)]
    with patch("tools._binance_klines._one_call", return_value=full_chunk):
        rows = fetch_klines_chained("BTCUSDT", "5m", 700)
    assert len(rows) == 700


def test_backtest_replay_routes_to_binance_when_source_binance():
    """_fetch_klines with source='binance' dispatches to the chained
    helper, not the WEEX executor."""
    from tools import backtest_replay
    with patch("tools._binance_klines.fetch_klines_chained",
                  return_value=[]) as mock_binance:
        backtest_replay._fetch_klines("BTCUSDT", "5m", 3000, source="binance")
    mock_binance.assert_called_once()
    # confirms the requested count flows through
    args, kwargs = mock_binance.call_args
    assert args[2] == 3000


def test_backtest_replay_routes_to_weex_by_default():
    """_fetch_klines without source kwarg dispatches to WEEX."""
    from tools import backtest_replay
    # Executor is imported INSIDE _fetch_klines, so patch at the source.
    with patch("executor.Executor") as MockEx:
        instance = MockEx.return_value
        instance.get_klines.return_value = []
        backtest_replay._fetch_klines("BTCUSDT", "5m", 500)
        instance.get_klines.assert_called_once_with("BTCUSDT", "5m", 500)
