"""P1.1 — exchange-native bracket orders (attach TP+SL at entry).

Root cause 1 from the Jul 2 research sweep: 60s poll-based exits let
price gap through stops (worst live loss -$2.02 on a 1% SL / $100
notional = 2x overshoot; ~$11-38 of pure execution bleed across 38
fleet SL-hits). Fix: the exchange matching engine enforces the bracket.

Three pieces:
 1. Executor.open_long/open_short accept tp_trigger_price alongside the
    existing sl_trigger_price. Working types per practitioner consensus:
    SL on MARK_PRICE (wick/stop-hunt immune), TP on CONTRACT_PRICE
    (real local prints can fill the target).
 2. risk.bracket_trigger_price() — pure helper mapping (entry, direction,
    reason, cfg) -> the trigger price an exchange-resident order would
    have filled at.
 3. Paper-fidelity: bot poll loops pass that trigger price as the exit
    fill instead of the (up to 60s late) polled price, so paper economics
    match the exchange-native architecture live will use.

Run: python -m pytest tests/test_exchange_native_brackets.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest


# ─── Executor entry orders carry both bracket legs ─────────────────────────

def _capture_executor():
    """Executor with _mutating_call monkeypatched to capture the body."""
    from executor import Executor
    ex = Executor(dry_run=True)
    captured = {}

    def _fake_mutating_call(endpoint, body=None, action_desc="", **kw):
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {"ok": True, "dry_run": True, "data": {}}

    ex._mutating_call = _fake_mutating_call
    return ex, captured


def test_open_long_attaches_sl_and_tp_triggers():
    ex, cap = _capture_executor()
    ex.open_long("BTCUSDT", "0.01",
                  sl_trigger_price="99000", tp_trigger_price="105000")
    body = cap["body"]
    assert body["slTriggerPrice"] == "99000"
    assert body["slWorkingType"] == "MARK_PRICE"
    assert body["tpTriggerPrice"] == "105000"
    assert body["tpWorkingType"] == "CONTRACT_PRICE"


def test_open_long_sl_only_backward_compat():
    """Existing callers that pass only sl_trigger_price keep working;
    no tp keys leak into the body."""
    ex, cap = _capture_executor()
    ex.open_long("BTCUSDT", "0.01", sl_trigger_price="99000")
    body = cap["body"]
    assert body["slTriggerPrice"] == "99000"
    assert "tpTriggerPrice" not in body
    assert "tpWorkingType" not in body


def test_open_long_no_triggers_omits_all_bracket_keys():
    ex, cap = _capture_executor()
    ex.open_long("BTCUSDT", "0.01")
    body = cap["body"]
    assert "slTriggerPrice" not in body
    assert "tpTriggerPrice" not in body


def test_open_short_attaches_sl_and_tp_triggers():
    ex, cap = _capture_executor()
    ex.open_short("ETHUSDT", "0.5",
                   sl_trigger_price="4200", tp_trigger_price="3900")
    body = cap["body"]
    assert body["slTriggerPrice"] == "4200"
    assert body["slWorkingType"] == "MARK_PRICE"
    assert body["tpTriggerPrice"] == "3900"
    assert body["tpWorkingType"] == "CONTRACT_PRICE"
    assert body["positionSide"] == "SHORT"


def test_open_short_no_triggers_backward_compat():
    ex, cap = _capture_executor()
    ex.open_short("ETHUSDT", "0.5")
    body = cap["body"]
    assert "slTriggerPrice" not in body
    assert "tpTriggerPrice" not in body


# ─── bracket_trigger_price — paper-fidelity fill helper ────────────────────

def test_trigger_price_long_sl():
    from risk import bracket_trigger_price
    cfg = {"sl_pct": 1.5, "tp_pct": 3.0}
    # LONG SL: entry * (1 - 1.5%)
    assert bracket_trigger_price(100.0, "LONG", "SL Hit", cfg) == pytest.approx(98.5)


def test_trigger_price_long_tp():
    from risk import bracket_trigger_price
    cfg = {"sl_pct": 1.5, "tp_pct": 3.0}
    assert bracket_trigger_price(100.0, "LONG", "TP Hit", cfg) == pytest.approx(103.0)


def test_trigger_price_short_sl():
    from risk import bracket_trigger_price
    cfg = {"sl_pct": 1.0, "tp_pct": 2.0}
    # SHORT SL: entry * (1 + 1%)
    assert bracket_trigger_price(100.0, "SHORT", "SL Hit", cfg) == pytest.approx(101.0)


def test_trigger_price_short_tp():
    from risk import bracket_trigger_price
    cfg = {"sl_pct": 1.0, "tp_pct": 2.0}
    assert bracket_trigger_price(100.0, "SHORT", "TP Hit", cfg) == pytest.approx(98.0)


def test_trigger_price_unknown_reason_returns_none():
    """Non-bracket exits (signal flip, stale, manual) → None so callers
    fall back to the live polled price."""
    from risk import bracket_trigger_price
    cfg = {"sl_pct": 1.0, "tp_pct": 2.0}
    assert bracket_trigger_price(100.0, "LONG", "Stale Exit", cfg) is None
    assert bracket_trigger_price(100.0, "LONG", "", cfg) is None
    assert bracket_trigger_price(100.0, "LONG", None, cfg) is None


def test_trigger_price_defaults_when_cfg_missing_keys():
    """cfg without sl_pct/tp_pct uses the same defaults the exit checkers use."""
    from risk import bracket_trigger_price
    # scalp defaults: 1.5 / 3.0
    assert bracket_trigger_price(
        100.0, "LONG", "SL Hit", {}, default_sl_pct=1.5, default_tp_pct=3.0
    ) == pytest.approx(98.5)


# ─── close functions honor the exit-price override ─────────────────────────

def test_close_scalp_position_uses_override_price(monkeypatch):
    """When run_cycle passes exit_price_override, the journal row records
    the trigger price, NOT the polled market price."""
    import scalp_main
    from unittest.mock import MagicMock

    executor = MagicMock()
    executor.get_symbol_price.return_value = 95.0   # late polled price
    state = {"positions": {"SCALP_BTC_5M": {
        "symbol": "BTCUSDT", "direction": "LONG",
        "entry_price": 100.0, "quantity": 0.01,
        "strategy": "BTC 5m Scalp", "entry_reason": "test",
        "atr_at_entry": 1.0,
    }}}

    logged = {}
    def _fake_log_trade(**kw):
        logged.update(kw)
    monkeypatch.setattr(scalp_main, "log_trade", _fake_log_trade)

    scalp_main.close_scalp_position(
        executor, state, "SCALP_BTC_5M", "SL Hit",
        exit_price_override=98.5)
    assert logged["exit_price"] == 98.5   # trigger, not 95.0


def test_close_crossover_position_uses_override_price(monkeypatch):
    import crossover_main
    from unittest.mock import MagicMock

    executor = MagicMock()
    executor.get_symbol_price.return_value = 90.0
    state = {"positions": {"CROSSOVER_ETH_1H": {
        "symbol": "ETHUSDT", "direction": "LONG",
        "entry_price": 100.0, "quantity": 0.05,
        "strategy": "ETH 1h Crossover", "entry_reason": "test",
        "atr_at_entry": 1.0,
    }}}

    logged = {}
    def _fake_log_trade(**kw):
        logged.update(kw)
    monkeypatch.setattr(crossover_main, "log_trade", _fake_log_trade)

    crossover_main.close_crossover_position(
        executor, state, "CROSSOVER_ETH_1H", "SL Hit",
        exit_price_override=99.0)
    assert logged["exit_price"] == 99.0


def test_close_scalp_position_falls_back_to_polled_price(monkeypatch):
    """No override → legacy behavior (polled price) for non-bracket exits."""
    import scalp_main
    from unittest.mock import MagicMock

    executor = MagicMock()
    executor.get_symbol_price.return_value = 97.25
    state = {"positions": {"SCALP_BTC_5M": {
        "symbol": "BTCUSDT", "direction": "LONG",
        "entry_price": 100.0, "quantity": 0.01,
        "strategy": "BTC 5m Scalp", "entry_reason": "test",
        "atr_at_entry": 1.0,
    }}}

    logged = {}
    monkeypatch.setattr(scalp_main, "log_trade",
                          lambda **kw: logged.update(kw))
    scalp_main.close_scalp_position(executor, state, "SCALP_BTC_5M", "manual")
    assert logged["exit_price"] == 97.25
