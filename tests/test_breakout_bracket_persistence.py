"""Jul 24 2026 day-21 review finding — breakout brackets not persisted.

The open BREAKOUT_DOGE_1H SHORT showed `SL — TP —` in fleet_review:
breakout attaches the exchange-resident SL at open (sl_trigger_price on
both directions) but register_entry never persisted sl_price /
bracket_kind. Two consequences:
  1. Dashboard + fleet_review can't display the bracket ("—").
  2. tools/risk_check.positions_missing_sl SKIPS the position as
     "legacy" (no marker fields) — a breakout position whose exchange
     SL failed to place would never be flagged by the sentinel.

Run: python -m pytest tests/test_breakout_bracket_persistence.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


class _FakeExec:
    def __init__(self):
        self.calls = {}

    def open_long(self, symbol, qty, sl_trigger_price=None):
        self.calls["side"] = "LONG"
        self.calls["sl"] = sl_trigger_price

    def open_short(self, symbol, qty, sl_trigger_price=None):
        self.calls["side"] = "SHORT"
        self.calls["sl"] = sl_trigger_price


def _open(monkeypatch, direction, cfg_extra=None):
    import breakout_main
    import regime
    monkeypatch.setattr(regime, "classify_from_df",
                          lambda df, cfg: {"vol": "low", "label": "test"})
    df = pd.DataFrame({"close": [100.0, 100.0, 100.0],
                        "atr":   [2.0, 2.0, 2.0]})
    state = {"positions": {}}
    cfg = {"symbol": "TESTUSDT", "sl_atr_mult": 1.5,
            "sl_atr_mult_short": 1.0,
            "strategy_name": "TEST 4H Breakout"}
    cfg.update(cfg_extra or {})
    ex = _FakeExec()
    breakout_main.open_breakout_position(ex, state, "TEST_4H", cfg,
                                           df, direction)
    return ex, state["positions"].get("BREAKOUT_TEST_4H")


def test_long_open_persists_bracket(monkeypatch):
    ex, pos = _open(monkeypatch, "LONG")
    assert pos is not None
    assert pos["bracket_kind"] == "atr_sl"
    assert pos["sl_price"] == pytest.approx(100.0 - 1.5 * 2.0)
    # persisted SL must equal the exchange-resident trigger
    assert float(ex.calls["sl"]) == pytest.approx(pos["sl_price"])


def test_short_open_persists_bracket(monkeypatch):
    ex, pos = _open(monkeypatch, "SHORT")
    assert pos is not None
    assert pos["bracket_kind"] == "atr_sl"
    assert pos["sl_price"] == pytest.approx(100.0 + 1.0 * 2.0)
    assert float(ex.calls["sl"]) == pytest.approx(pos["sl_price"])


def test_sentinel_now_guards_breakout_positions(monkeypatch):
    """With the marker persisted, a breakout position that somehow lost
    its SL gets FLAGGED instead of skipped as legacy."""
    from tools.risk_check import positions_missing_sl
    flagged = positions_missing_sl({
        "BREAKOUT_TEST_4H": {"bracket_kind": "atr_sl", "sl_price": None},
    })
    assert flagged == ["BREAKOUT_TEST_4H"]
