"""P3.6 — fleet-level risk gates.

1. BTC-ETH 30d rolling-returns correlation gate (pure helpers in regime.py).
   Practitioner research (Jul 2026 sweep): alt trend entries taken only
   while BTC-ETH correlation is high roughly doubled PF — correlation
   breakdown precedes rotational chop where trend signals fail.
2. Percent-based daily drawdown breaker in kill_switch.py:
   3% of INITIAL_CAPITAL, composed with the legacy fixed-USD floor
   (whichever is tighter fires first).

Run: python -m pytest tests/test_p36_fleet_gates.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


# ─── BTC-ETH correlation helpers ───────────────────────────────────────────

def _trend(n, step, base=100.0, wobble=None):
    out, x = [], base
    for i in range(n):
        x += step + (wobble[i % len(wobble)] if wobble else 0.0)
        out.append(x)
    return out


def test_correlation_high_for_comoving_series():
    from regime import rolling_returns_correlation
    a = _trend(40, 1.0, base=100.0, wobble=[0.3, -0.2, 0.1, -0.3])
    b = [x * 0.05 + 2.0 for x in a]   # linear map of a → returns corr ≈ 1
    corr = rolling_returns_correlation(a, b, window=30)
    assert corr is not None
    assert corr > 0.9


def test_correlation_negative_for_mirrored_series():
    from regime import rolling_returns_correlation
    a = _trend(40, 1.0, base=200.0, wobble=[0.5, -0.4])
    b = [400.0 - x for x in a]        # perfect mirror → corr ≈ -1
    corr = rolling_returns_correlation(a, b, window=30)
    assert corr is not None
    assert corr < -0.9


def test_correlation_none_on_insufficient_data():
    from regime import rolling_returns_correlation
    assert rolling_returns_correlation([1, 2, 3], [1, 2, 3], window=30) is None
    assert rolling_returns_correlation(None, None, window=30) is None


def test_correlation_none_on_zero_variance():
    from regime import rolling_returns_correlation
    flat = [100.0] * 40
    moving = _trend(40, 1.0)
    assert rolling_returns_correlation(flat, moving, window=30) is None


def test_corr_gate_allows_semantics():
    from regime import corr_gate_allows
    assert corr_gate_allows(0.85, min_corr=0.6) is True
    assert corr_gate_allows(0.30, min_corr=0.6) is False
    # missing data must degrade to ALLOW, never block on a fetch problem
    assert corr_gate_allows(None, min_corr=0.6) is True


# ─── Percent-based daily drawdown breaker ──────────────────────────────────

def test_daily_dd_threshold_takes_tighter_of_usd_and_pct():
    import kill_switch as ks
    # INITIAL_CAPITAL=5000, 3% → -150, tighter than the legacy -500 floor
    thr = ks._daily_dd_threshold_usd()
    assert thr == pytest.approx(-150.0)


def test_daily_dd_threshold_falls_back_to_usd_without_pct(monkeypatch):
    import kill_switch as ks
    monkeypatch.setattr(ks, "MAX_DAILY_DRAWDOWN_PCT", None)
    assert ks._daily_dd_threshold_usd() == pytest.approx(ks.MAX_DAILY_DRAWDOWN_USD)


def test_daily_dd_threshold_usd_floor_when_pct_looser(monkeypatch):
    import kill_switch as ks
    # A silly-loose 50% (= -2500) must not loosen the -500 fixed floor
    monkeypatch.setattr(ks, "MAX_DAILY_DRAWDOWN_PCT", 50.0)
    assert ks._daily_dd_threshold_usd() == pytest.approx(-500.0)


# ─── Cycle-level BTC-ETH corr fetch (momentum main wiring) ─────────────────

class _StubExecutor:
    def __init__(self, closes_by_symbol):
        self._closes = closes_by_symbol

    def get_klines(self, symbol, interval, count):
        closes = self._closes.get(symbol)
        if closes is None:
            raise RuntimeError("fetch failed")
        # WEEX positional layout — close at index 4
        return [[i, c, c, c, c, 100.0] for i, c in enumerate(closes)]


def test_fetch_btc_eth_corr_comoving():
    from main import _fetch_btc_eth_corr
    a = _trend(40, 1.0, base=100.0, wobble=[0.3, -0.2, 0.1, -0.3])
    b = [x * 0.05 + 2.0 for x in a]
    ex = _StubExecutor({"BTCUSDT": a, "ETHUSDT": b})
    corr = _fetch_btc_eth_corr(ex, window=30)
    assert corr is not None and corr > 0.9


def test_fetch_btc_eth_corr_none_on_fetch_failure():
    from main import _fetch_btc_eth_corr
    ex = _StubExecutor({"BTCUSDT": _trend(40, 1.0)})   # ETH fetch raises
    assert _fetch_btc_eth_corr(ex, window=30) is None
