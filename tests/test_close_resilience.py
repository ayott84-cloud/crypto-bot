"""Unit tests for close-path resilience to journal-write failures.

Phase A.2 of the comprehensive enhancement plan. If log_trade() raises
(SQLite locked, disk full, schema mismatch, etc.), close_*_position must
still return True after state is cleaned — otherwise the caller bot
treats the close as failed, the position lingers in state, and the
manage_open_positions loop will keep retrying forever, multiplying the
journal-write failures into a runaway loop.

The peer-review-corrected approach: keep the existing
register_exit → save_state → log_trade order (so an exception between
state-strip and journal-write produces an at-most ORPHAN row — closed
in state, open in journal — which the A.3 reconciler catches). Wrap
log_trade itself in try/except that logs a warning and swallows.

Run: python -m pytest tests/test_close_resilience.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

import whale_main
import funding_main


# ─── Fake executor (close paths only) ───────────────────────────────────────

class FakeExecutor:
    def __init__(self, mark_price: float = 81000.0):
        self.mark_price = mark_price
        self.cancelled = []
        self.closed = []

    def cancel_pending_orders(self, symbol):
        self.cancelled.append(symbol)

    def close_long_full(self, symbol):
        self.closed.append(("LONG", symbol))
        return {"ok": True}

    def close_short_full(self, symbol):
        self.closed.append(("SHORT", symbol))
        return {"ok": True}

    def get_symbol_price(self, symbol):
        return self.mark_price

    def get_account_balance(self):
        return {"balance": 0}


def _whale_state(key="WHALE_BTC", direction="LONG"):
    return {
        "positions": {
            key: {
                "symbol": "BTCUSDT",
                "direction": direction,
                "entry_price": 80000.0,
                "quantity": "0.01",
                "strategy": "Whale Track BTC LONG",
            }
        },
        "whale_cooldowns": {},
    }


def _funding_state(key="FUNDING_ETH", direction="LONG"):
    return {
        "positions": {
            key: {
                "symbol": "ETHUSDT",
                "direction": direction,
                "entry_price": 2400.0,
                "quantity": "0.5",
                "strategy": "Funding Fade ETH LONG",
            }
        },
        "funding_cooldowns": {},
    }


# ─── whale_main.close_whale_position ────────────────────────────────────────

def test_close_whale_swallows_log_trade_error(monkeypatch):
    """log_trade raising must not propagate; close still returns True; state cleaned."""
    state = _whale_state()

    def boom(*a, **kw):
        raise RuntimeError("simulated journal write failure")

    monkeypatch.setattr(whale_main, "log_trade", boom)
    monkeypatch.setattr(whale_main, "save_state", lambda s, owner="momentum": None)
    # Disable notifier (irrelevant to this test)
    monkeypatch.setattr(whale_main, "notify_trade_closed", None)

    ok = whale_main.close_whale_position(FakeExecutor(), state, "WHALE_BTC", "test exit")

    assert ok is True
    assert "WHALE_BTC" not in state["positions"]


def test_close_whale_state_still_cleaned_when_log_trade_raises(monkeypatch):
    """The close-then-log order means state IS stripped even on log failure.

    This is intentional per the peer-review-corrected plan — better to have an
    orphan journal row (catchable by A.3 reconciler) than to leave the position
    open and have the management loop retry every cycle.
    """
    state = _whale_state(direction="SHORT")
    monkeypatch.setattr(whale_main, "log_trade",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")))
    monkeypatch.setattr(whale_main, "save_state", lambda s, owner="momentum": None)
    monkeypatch.setattr(whale_main, "notify_trade_closed", None)

    whale_main.close_whale_position(FakeExecutor(), state, "WHALE_BTC", "SL hit")

    assert "WHALE_BTC" not in state["positions"]
    # whale_cooldowns must have been recorded BEFORE register_exit
    assert "BTC" in state["whale_cooldowns"]


def test_close_whale_returns_true_on_normal_path(monkeypatch):
    """Sanity: the happy path still returns True when log_trade succeeds."""
    state = _whale_state()
    calls = []
    monkeypatch.setattr(whale_main, "log_trade", lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr(whale_main, "save_state", lambda s, owner="momentum": None)
    monkeypatch.setattr(whale_main, "notify_trade_closed", None)

    ok = whale_main.close_whale_position(FakeExecutor(), state, "WHALE_BTC", "test")

    assert ok is True
    assert len(calls) == 1
    assert calls[0]["direction"] == "LONG"


# ─── funding_main.close_funding_position ────────────────────────────────────

def test_close_funding_swallows_log_trade_error(monkeypatch):
    state = _funding_state()

    def boom(*a, **kw):
        raise RuntimeError("simulated journal write failure")

    monkeypatch.setattr(funding_main, "log_trade", boom)
    monkeypatch.setattr(funding_main, "save_state", lambda s, owner="momentum": None)
    monkeypatch.setattr(funding_main, "notify_trade_closed", None)

    ok = funding_main.close_funding_position(FakeExecutor(mark_price=2500.0),
                                              state, "FUNDING_ETH", "time-stop")

    assert ok is True
    assert "FUNDING_ETH" not in state["positions"]


def test_close_funding_returns_true_on_normal_path(monkeypatch):
    state = _funding_state(direction="SHORT")
    calls = []
    monkeypatch.setattr(funding_main, "log_trade", lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr(funding_main, "save_state", lambda s, owner="momentum": None)
    monkeypatch.setattr(funding_main, "notify_trade_closed", None)

    ok = funding_main.close_funding_position(FakeExecutor(mark_price=2300.0),
                                              state, "FUNDING_ETH", "TP hit")

    assert ok is True
    assert len(calls) == 1
    assert calls[0]["direction"] == "SHORT"
