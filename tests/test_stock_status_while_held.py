"""Aug 22 2026 — signal_status froze while a position was held.

The reversion entry loop skips an asset that already has an open
position, and it does so BEFORE writing signal_status. So from the
moment QQQ_REV and SPY_REV opened on Aug 19, their status rows stopped
updating: three days later fleet_review still reported
bar=2026-08-17, would_enter=True.

That is not merely cosmetic. It is the panel an operator reads to answer
"is this sleeve alive and what is it seeing", and a frozen row answers
confidently and wrongly. It is also what made the forming-bar fix
unverifiable — the decision bar is reported through this field, so a
held position hid whether the fix took.

A held asset now reports blocked_by="position_open" with a live bar and
timestamp. It is still not entering, and now it says so in the present
tense.

Run: python -m pytest tests/test_stock_status_while_held.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pytest

pd = pytest.importorskip("pandas")

import stock_daily_main as sdm
from stock_config import STOCK_REV_ASSETS


def _bars(n=260):
    idx = pd.bdate_range(end="2026-08-21", periods=n)
    v = [100.0 + i * 0.01 for i in range(n)]
    return pd.DataFrame({"open": v, "high": v, "low": v, "close": v,
                          "volume": [1e6] * n}, index=idx)


class _Exec:
    def get_klines(self, symbol, interval="1d", limit=300):
        from tools._equity_bars import to_positional_rows
        return to_positional_rows(_bars(), "1d")

    def get_symbol_price(self, symbol):
        return 101.0

    def open_long(self, *a, **kw):
        return {"ok": True}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(sdm, "_HEARTBEAT_FILE", tmp_path / ".hb")
    monkeypatch.setattr(sdm, "STOCK_PAUSED", False)
    monkeypatch.setattr(sdm.mc, "is_market_open", lambda ts=None: True)
    monkeypatch.setattr(sdm, "in_closing_window", lambda *a, **kw: True)
    monkeypatch.setattr(sdm, "_is_rebalance_day", lambda *a, **kw: False)
    monkeypatch.setattr(sdm, "should_pause", lambda o: type(
        "S", (), {"paused": False, "reason": ""})())
    monkeypatch.setattr(sdm, "save_state", lambda *a, **kw: None)


def _held_state():
    name = next(iter(STOCK_REV_ASSETS))
    cfg = STOCK_REV_ASSETS[name]
    key = sdm._state_key(name, "rev")
    return name, {"positions": {key: {
        "symbol": cfg["symbol"], "sleeve": "rev", "direction": "LONG",
        "entry_price": 100.0, "quantity": 2}}}


# ─── The freeze ──────────────────────────────────────────────────────────

def test_a_held_asset_still_reports_a_status():
    name, state = _held_state()
    sdm.run_cycle(_Exec(), state)
    assert name in (state.get("stock_signal_status") or {}), \
        "status froze the moment the position opened"


def test_a_held_asset_says_why_it_is_not_entering():
    name, state = _held_state()
    sdm.run_cycle(_Exec(), state)
    row = state["stock_signal_status"][name]
    assert row["would_enter"] is False
    assert row["blocked_by"] == "position_open"


def test_the_reported_bar_is_current_not_the_entry_bar():
    """The decision bar is reported through this field, so a frozen row
    made the forming-bar fix unverifiable."""
    name, state = _held_state()
    sdm.run_cycle(_Exec(), state)
    row = state["stock_signal_status"][name]
    assert row["bar"] is not None
    assert row["checked_at"]


def test_holding_does_not_open_a_second_position():
    """Reporting must not become acting."""
    name, state = _held_state()
    before = dict(state["positions"])
    sdm.run_cycle(_Exec(), state)
    assert state["positions"].keys() == before.keys()


def test_an_unheld_asset_is_unaffected():
    _name, state = _held_state()
    sdm.run_cycle(_Exec(), state)
    others = [k for k in STOCK_REV_ASSETS if k != _name]
    if others:
        row = (state.get("stock_signal_status") or {}).get(others[0])
        assert row is not None
        assert row.get("blocked_by") != "position_open"
