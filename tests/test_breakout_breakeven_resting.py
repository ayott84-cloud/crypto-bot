"""Aug 14 2026 — breakout's breakeven ratchet never moved the real stop.

open_breakout_position places an exchange-resident SL at
entry -/+ sl_mult x ATR and persists it as pos["sl_price"]. When the
L.3.1 ratchet fires, it flipped a boolean that only check_breakout_exit
reads. The resting order stayed at the original ATR level.

Two consequences, and the smaller one is the visible one:

  * The exit became a SOFT stop evaluated on the bar CLOSE. By the time
    a bar closes at or below entry, price is below entry — so a
    "breakeven" exit can never be flat. The live journal shows BE Hit at
    -$1.58 where a true breakeven would be about -$0.20 in costs.
  * The larger one: between the ratchet firing and the soft exit, the
    only order actually resting at the venue is the WIDE original stop.
    An intrabar collapse blows through the breakeven the code believes
    in and fills at the ATR stop instead. In DRY_RUN that is invisible.

Correcting the magnitude claim in the record: this is one trade in ten
and roughly $1.38 of the -$26.01 window. It is worth fixing because a
stop that lies about where it rests is a risk defect, NOT because it
explains breakout's losses. It does not.

Run: python -m pytest tests/test_breakout_breakeven_resting.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pytest.importorskip("pandas")

import breakout_main as bm


class _Executor:
    """Records the calls that matter, tolerates the ones that don't."""

    def __init__(self):
        self.sl_orders = []
        self.sl_orders_short = []
        self.cancelled = []

    def place_sl_order(self, symbol, trigger_price, quantity):
        self.sl_orders.append((symbol, trigger_price, quantity))
        return {"ok": True}

    def place_sl_order_short(self, symbol, trigger_price, quantity):
        self.sl_orders_short.append((symbol, trigger_price, quantity))
        return {"ok": True}

    def cancel_pending_orders(self, symbol):
        self.cancelled.append(symbol)
        return {"ok": True}


def _pos(direction="LONG", entry=100.0, sl=95.0):
    return {"symbol": "ETHUSDT", "direction": direction,
            "entry_price": entry, "quantity": 1.5,
            "atr_at_entry": 2.0, "sl_price": sl,
            "bracket_kind": "atr_sl"}


# ─── The ratchet must move the real order ────────────────────────────────

def test_ratchet_replaces_the_resting_stop_at_entry():
    ex, pos = _Executor(), _pos()
    bm.move_stop_to_breakeven(ex, "ETH_4H", pos)
    assert ex.cancelled == ["ETHUSDT"], "old stop left resting at the venue"
    assert len(ex.sl_orders) == 1
    symbol, trigger, qty = ex.sl_orders[0]
    assert symbol == "ETHUSDT"
    assert float(trigger) == pytest.approx(100.0), \
        "the new stop is not at the entry price"
    assert float(qty) == pytest.approx(1.5)


def test_shorts_use_the_short_stop_primitive():
    """place_sl_order hardcodes positionSide=LONG; a SHORT sent through
    it would be rejected or, worse, close the wrong side."""
    ex, pos = _Executor(), _pos(direction="SHORT", entry=100.0, sl=105.0)
    bm.move_stop_to_breakeven(ex, "ETH_4H", pos)
    assert ex.sl_orders == []
    assert len(ex.sl_orders_short) == 1
    assert float(ex.sl_orders_short[0][1]) == pytest.approx(100.0)


def test_persisted_sl_price_is_updated_to_match():
    """The risk sentinel reads pos['sl_price']. Leaving it stale means
    the dashboard reports a bracket that is not the one resting."""
    ex, pos = _Executor(), _pos()
    bm.move_stop_to_breakeven(ex, "ETH_4H", pos)
    assert pos["sl_price"] == pytest.approx(100.0)
    assert pos.get("bracket_kind") == "breakeven"


def test_the_move_is_recorded_on_the_position():
    ex, pos = _Executor(), _pos()
    bm.move_stop_to_breakeven(ex, "ETH_4H", pos)
    assert pos.get("breakeven_stop_placed") is True


def test_a_failed_replacement_does_not_claim_success():
    """If the venue refuses, the position must NOT be marked as
    protected at breakeven — believing in a stop that isn't there is
    the whole defect being fixed."""
    class Broken(_Executor):
        def place_sl_order(self, *a, **k):
            raise RuntimeError("venue rejected")

    pos = _pos()
    bm.move_stop_to_breakeven(Broken(), "ETH_4H", pos)
    assert pos.get("breakeven_stop_placed") is not True
    assert pos["sl_price"] == pytest.approx(95.0), \
        "sl_price advertised a stop that was never placed"


def test_replacement_is_not_attempted_twice():
    ex, pos = _Executor(), _pos()
    bm.move_stop_to_breakeven(ex, "ETH_4H", pos)
    bm.move_stop_to_breakeven(ex, "ETH_4H", pos)
    assert len(ex.sl_orders) == 1, "re-placed a stop that was already at BE"


def test_a_position_without_a_quantity_is_skipped_not_guessed():
    ex = _Executor()
    pos = _pos()
    pos["quantity"] = 0
    bm.move_stop_to_breakeven(ex, "ETH_4H", pos)
    assert ex.sl_orders == [] and ex.sl_orders_short == []
    assert pos.get("breakeven_stop_placed") is not True


# ─── Wired into the cycle ────────────────────────────────────────────────

def test_run_cycle_moves_the_stop_when_the_ratchet_fires():
    import inspect
    src = inspect.getsource(bm.run_cycle)
    assert "move_stop_to_breakeven(" in src, \
        "the ratchet still only flips a boolean"
