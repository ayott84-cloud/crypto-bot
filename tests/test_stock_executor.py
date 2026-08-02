"""Module 2 Phase S3 — Alpaca paper executor.

Duck-typed to the same surface the bot mains already call on the WEEX
Executor, so stock_daily_main reads like breakout_main and the shared
helpers (position_manager sizing, reconcile) work unchanged.

THREE SAFETY PROPERTIES, PINNED HARDEST:
  1. PAPER ONLY. The base URL is paper-api.alpaca.markets and reaching
     live requires an explicit, loud opt-in. The crypto module has run
     DRY_RUN for months precisely because the flip must be deliberate;
     a stocks module that could silently point at real money would
     undo that discipline.
  2. DRY_RUN guard. Mirrors executor._mutating_call: when dry_run is
     set, no mutating request leaves the process.
  3. LONG/FLAT ONLY. The daily sleeves never short, so the short
     methods raise rather than quietly succeeding — Reg SHO locates,
     borrow fees and SSR are unmodeled and a silent short would be
     trading a strategy we never validated.

Run: python -m pytest tests/test_stock_executor.py -v
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


@pytest.fixture(autouse=True)
def _creds(monkeypatch):
    monkeypatch.setattr(se, "ALPACA_API_KEY", "test-key")
    monkeypatch.setattr(se, "ALPACA_API_SECRET", "test-secret")


# ─── Safety: paper only ───────────────────────────────────────────────────

def test_default_base_url_is_paper():
    ex = se.StockExecutor()
    assert "paper-api.alpaca.markets" in ex.base_url
    assert "live" not in ex.base_url


def test_live_requires_explicit_opt_in_and_is_loud(monkeypatch, capsys):
    """Reaching real money must be a deliberate act, never a default or
    a typo in an env var."""
    with pytest.raises(se.LiveTradingBlocked):
        se.StockExecutor(paper=False)

    monkeypatch.setattr(se, "ALLOW_LIVE_TRADING", True)
    ex = se.StockExecutor(paper=False)
    assert "paper" not in ex.base_url
    assert "LIVE" in capsys.readouterr().out.upper()


def test_missing_credentials_raise(monkeypatch):
    monkeypatch.setattr(se, "ALPACA_API_KEY", "")
    monkeypatch.setattr(se, "ALPACA_API_SECRET", "")
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    with pytest.raises(se.CredentialsMissing):
        se.StockExecutor()


# ─── Safety: DRY_RUN guard ────────────────────────────────────────────────

def test_dry_run_blocks_every_mutating_call(monkeypatch):
    sent = []
    monkeypatch.setattr(se, "_http", lambda *a, **kw: sent.append(a) or {})
    ex = se.StockExecutor(dry_run=True)
    r1 = ex.open_long("SPY", 10)
    r2 = ex.close_long_full("SPY")
    r3 = ex.cancel_pending_orders("SPY")
    assert sent == [], "a mutating request escaped in DRY_RUN"
    for r in (r1, r2, r3):
        assert r.get("dry_run") is True and r.get("ok") is True


def test_dry_run_still_allows_reads(monkeypatch):
    calls = []

    def fake_http(method, url, headers=None, params=None, body=None):
        calls.append(method)
        return {"bars": []}

    monkeypatch.setattr(se, "_http", fake_http)
    ex = se.StockExecutor(dry_run=True)
    ex.get_klines("SPY", "1d", 5)
    assert calls and all(m == "GET" for m in calls)


# ─── Safety: long/flat only ───────────────────────────────────────────────

def test_short_methods_refuse():
    ex = se.StockExecutor(dry_run=True)
    for fn in ("open_short", "close_short_full", "place_sl_order_short"):
        with pytest.raises(se.ShortingNotSupported):
            getattr(ex, fn)("SPY", 1)


# ─── Kline shape (the harness contract) ──────────────────────────────────

def test_get_klines_returns_the_11_column_positional_shape(monkeypatch):
    payload = {"bars": [
        {"t": "2026-07-29T04:00:00Z", "o": 100.0, "h": 101.0, "l": 99.0,
          "c": 100.5, "v": 1_000_000},
        {"t": "2026-07-30T04:00:00Z", "o": 100.5, "h": 102.0, "l": 100.0,
          "c": 101.5, "v": 1_100_000},
    ], "next_page_token": None}
    monkeypatch.setattr(se, "_http", lambda *a, **kw: payload)
    rows = se.StockExecutor(dry_run=True).get_klines("SPY", "1d", 2)
    assert all(len(r) == 11 for r in rows)
    from signals import build_dataframe
    df = build_dataframe(rows)
    assert "close_time" in df.columns
    assert float(df["close"].iloc[-1]) == pytest.approx(101.5)


def test_get_klines_is_chronological(monkeypatch):
    payload = {"bars": [
        {"t": "2026-07-30T04:00:00Z", "o": 2, "h": 2, "l": 2, "c": 2, "v": 1},
        {"t": "2026-07-29T04:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
    ]}
    monkeypatch.setattr(se, "_http", lambda *a, **kw: payload)
    rows = se.StockExecutor(dry_run=True).get_klines("SPY", "1d", 2)
    assert rows[0][0] < rows[1][0], "bars not sorted oldest-first"


# ─── Account + positions ─────────────────────────────────────────────────

def test_get_account_balance_exposes_a_balance_key(monkeypatch):
    """Every close path reads .get('balance') — the crypto contract."""
    monkeypatch.setattr(se, "_http", lambda *a, **kw: {
        "equity": "10123.45", "cash": "9000.00", "status": "ACTIVE"})
    bal = se.StockExecutor(dry_run=True).get_account_balance()
    assert bal["balance"] == pytest.approx(10123.45)


def test_get_all_positions_normalizes_to_the_crypto_shape(monkeypatch):
    monkeypatch.setattr(se, "_http", lambda *a, **kw: [
        {"symbol": "SPY", "qty": "12", "avg_entry_price": "500.25",
          "side": "long"}])
    pos = se.StockExecutor(dry_run=True).get_all_positions()
    assert pos[0]["symbol"] == "SPY"
    assert float(pos[0]["positionAmt"]) == pytest.approx(12.0)


def test_get_symbol_price_returns_float_or_none(monkeypatch):
    monkeypatch.setattr(se, "_http", lambda *a, **kw: {
        "trade": {"p": 501.23}})
    assert se.StockExecutor(dry_run=True).get_symbol_price("SPY") == \
        pytest.approx(501.23)
    monkeypatch.setattr(se, "_http", lambda *a, **kw: {})
    assert se.StockExecutor(dry_run=True).get_symbol_price("SPY") is None


# ─── Equity-specific sizing ──────────────────────────────────────────────

def test_whole_share_sizing_by_default():
    """Fractional shares exist at Alpaca but complicate reconciliation
    and are not needed at our size."""
    ex = se.StockExecutor(dry_run=True)
    assert ex.get_qty_step("SPY") == 1.0
    assert ex.get_min_qty("SPY") == 1.0


def test_tick_size_is_a_penny_for_normal_priced_equities():
    assert se.StockExecutor(dry_run=True).get_tick_size("SPY") == \
        pytest.approx(0.01)


# ─── Crypto-only concepts must not silently return something ─────────────

def test_perp_only_methods_raise_not_return_zero():
    ex = se.StockExecutor(dry_run=True)
    with pytest.raises(NotImplementedError):
        ex.get_funding_rate("SPY")
