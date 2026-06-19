"""Regression test for the long-standing register_entry signature bug
that caused every breakout entry attempt to silently fail since Phase G.

Symptom in production logs:
  TypeError: register_entry() got an unexpected keyword argument 'direction'

Root cause: register_entry signature accepted only the momentum-style
kwargs. breakout_main / pair_main / reversal_main were each passing
bot-specific extras (direction, entry_ratio, entry_z, bars_held) that
crashed the call inside their try/except entry blocks. The crashes
were caught + logged at ERROR but never surfaced as alerts.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

from position_manager import register_entry


def _empty_state() -> dict:
    return {"positions": {}}


def test_register_entry_accepts_direction():
    state = _empty_state()
    register_entry(
        state, "BREAKOUT_BTC_1H",
        entry_price=80000, atr_at_entry=200,
        quantity="0.001", strategy="BTC 1H Breakout",
        entry_reason="Donchian break LONG",
        symbol="BTCUSDT",
        direction="LONG",
    )
    assert state["positions"]["BREAKOUT_BTC_1H"]["direction"] == "LONG"


def test_register_entry_accepts_short_direction():
    state = _empty_state()
    register_entry(
        state, "BREAKOUT_ETH_1H",
        entry_price=3000, atr_at_entry=10,
        quantity="0.05", strategy="ETH 1H Breakout",
        entry_reason="Donchian break SHORT",
        symbol="ETHUSDT",
        direction="SHORT",
    )
    assert state["positions"]["BREAKOUT_ETH_1H"]["direction"] == "SHORT"


def test_register_entry_default_direction_is_long():
    """Momentum + funding never pass direction; default must remain LONG
    for backwards-compat."""
    state = _empty_state()
    register_entry(
        state, "BTC_4H",
        entry_price=80000, atr_at_entry=200,
        quantity="0.001", strategy="BTC 4H Momentum v2",
        symbol="BTCUSDT",
    )
    assert state["positions"]["BTC_4H"]["direction"] == "LONG"


def test_register_entry_absorbs_pair_extras():
    """pair_main passes entry_ratio + entry_z. These must persist on
    the position dict, not crash the call."""
    state = _empty_state()
    register_entry(
        state, "PAIR_ETHBTC_LONG_LEG",
        entry_price=2000, atr_at_entry=0.0,
        quantity="0.5", strategy="Pair",
        symbol="ETHUSDT", direction="LONG",
        entry_ratio=0.06, entry_z=2.3,
    )
    pos = state["positions"]["PAIR_ETHBTC_LONG_LEG"]
    assert pos["entry_ratio"] == pytest.approx(0.06)
    assert pos["entry_z"] == pytest.approx(2.3)


def test_register_entry_absorbs_reversal_extras():
    """reversal_main passes bars_held. Must absorb, not crash."""
    state = _empty_state()
    register_entry(
        state, "REVERSAL_BTC_1D_LONG",
        entry_price=80000, atr_at_entry=2000,
        quantity="0.001", strategy="Reversal",
        symbol="BTCUSDT", direction="LONG",
        bars_held=0,
    )
    pos = state["positions"]["REVERSAL_BTC_1D_LONG"]
    assert pos["bars_held"] == 0
