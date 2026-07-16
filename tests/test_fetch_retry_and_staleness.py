"""Jul 16 2026 evening fixes — two false signals from the droplet runs.

1. tools/_binance_klines: a transient SSLEOFError mid-chain returned []
   from _one_call, which fetch_klines_chained read as "history
   exhausted" — the momentum re-window and trailing A/B ran on SILENTLY
   TRUNCATED windows (SOL's chain died at Feb 2025, mid-history).
   Transient transport errors must retry with backoff; only after
   retries exhaust may the chain stop, and then LOUDLY.

2. tools/risk_check: funding's cycle is hourly, so its heartbeat is
   30-60 min old for the back half of every cycle — the 30-min global
   staleness bar flagged a HEALTHY bot as STALE (and I had the operator
   restart it for nothing). Staleness must be cycle-aware per owner.

Run: python -m pytest tests/test_fetch_retry_and_staleness.py -v
"""

from __future__ import annotations

import sys
import time as _time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

import requests


# ─── 1. fetch retry-with-backoff ───────────────────────────────────────────

class _FakeResp:
    def __init__(self, status_code=200, rows=None):
        self.status_code = status_code
        self._rows = rows if rows is not None else []
        self.text = "fake"

    def json(self):
        return self._rows


def _cb_row(ts_s, price=100.0):
    # Coinbase row: [time_seconds, low, high, open, close, volume]
    return [ts_s, price - 1, price + 1, price, price, 5.0]


def test_transient_error_retries_then_succeeds(monkeypatch):
    import tools._binance_klines as bk
    calls = {"n": 0}

    def flaky_get(*a, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.SSLError("EOF occurred in violation")
        return _FakeResp(rows=[_cb_row(1_700_000_000)])

    monkeypatch.setattr(bk.requests, "get", flaky_get)
    monkeypatch.setattr(bk.time, "sleep", lambda s: None)
    rows = bk._one_call("BTCUSDT", "1h", end_time_ms=None, limit=300)
    assert calls["n"] == 3
    assert len(rows) == 1
    assert rows[0][0] == 1_700_000_000 * 1000


def test_retryable_http_status_retries(monkeypatch):
    import tools._binance_klines as bk
    calls = {"n": 0}

    def flaky_get(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResp(status_code=429)
        return _FakeResp(rows=[_cb_row(1_700_000_000)])

    monkeypatch.setattr(bk.requests, "get", flaky_get)
    monkeypatch.setattr(bk.time, "sleep", lambda s: None)
    rows = bk._one_call("BTCUSDT", "1h", end_time_ms=None, limit=300)
    assert calls["n"] == 2
    assert len(rows) == 1


def test_exhausted_retries_raise_transient_error(monkeypatch):
    import tools._binance_klines as bk

    def always_fail(*a, **kw):
        raise requests.exceptions.SSLError("EOF occurred in violation")

    monkeypatch.setattr(bk.requests, "get", always_fail)
    monkeypatch.setattr(bk.time, "sleep", lambda s: None)
    with pytest.raises(bk.TransientFetchError):
        bk._one_call("BTCUSDT", "1h", end_time_ms=None, limit=300)


def test_chain_truncation_is_loud_and_returns_partial(monkeypatch, caplog):
    """A chain that dies mid-history must return what it has AND warn
    with the word TRUNCATED — never masquerade as a full window."""
    import tools._binance_klines as bk
    calls = {"n": 0}
    hour_ms = 3_600_000

    def one_call(symbol, interval, end_time_ms, limit):
        calls["n"] += 1
        if calls["n"] == 1:
            base = 1_700_000_000_000
            return [[base + i * hour_ms, "1", "2", "0.5", "1", "1",
                      base + (i + 1) * hour_ms - 1, "0", "0", "0", "0"]
                     for i in range(300)]
        raise bk.TransientFetchError("SSL EOF after retries")

    monkeypatch.setattr(bk, "_one_call", one_call)
    with caplog.at_level("WARNING"):
        rows = bk.fetch_klines_chained("BTCUSDT", "1h", 900)
    assert len(rows) == 300          # partial survives
    assert any("TRUNCATED" in r.message for r in caplog.records)


def test_non_retryable_http_is_still_empty(monkeypatch):
    """A 404 (bad product) is not transient — no retry storm, empty."""
    import tools._binance_klines as bk
    calls = {"n": 0}

    def get_404(*a, **kw):
        calls["n"] += 1
        return _FakeResp(status_code=404)

    monkeypatch.setattr(bk.requests, "get", get_404)
    monkeypatch.setattr(bk.time, "sleep", lambda s: None)
    rows = bk._one_call("BTCUSDT", "1h", end_time_ms=None, limit=300)
    assert rows == []
    assert calls["n"] == 1


# ─── 2. cycle-aware heartbeat staleness ────────────────────────────────────

def _hb(tmp_path, name, age_s):
    import os
    p = tmp_path / name
    p.touch()
    old = _time.time() - age_s
    os.utime(p, (old, old))
    return p


def test_funding_heartbeat_40min_is_fresh(tmp_path, monkeypatch):
    """Funding cycles hourly — 40 minutes since the last beat is a
    HEALTHY bot mid-sleep, not a wedge."""
    import tools.risk_check as rc
    monkeypatch.setattr(rc, "_parked_owners", lambda: set())
    rows = rc.classify_heartbeats([_hb(tmp_path, ".funding_heartbeat", 40 * 60)])
    assert rows[0]["stale"] is False


def test_funding_heartbeat_3h_is_stale(tmp_path, monkeypatch):
    import tools.risk_check as rc
    monkeypatch.setattr(rc, "_parked_owners", lambda: set())
    rows = rc.classify_heartbeats([_hb(tmp_path, ".funding_heartbeat", 3 * 3600)])
    assert rows[0]["stale"] is True


def test_default_threshold_unchanged_for_fast_bots(tmp_path, monkeypatch):
    """Scalp beats every cycle (~minutes) — 40 min old is still a wedge."""
    import tools.risk_check as rc
    monkeypatch.setattr(rc, "_parked_owners", lambda: set())
    rows = rc.classify_heartbeats([_hb(tmp_path, ".scalp_heartbeat", 40 * 60)])
    assert rows[0]["stale"] is True
