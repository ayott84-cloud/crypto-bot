"""Aug 22 2026 — make momentum's RSI gate A/B-able.

BTC ran +18.7% over 30 days and the fleet lost money. Momentum's
blockers say why: rsi_crossover held 8 of 10 configs flat while the
trend gate was open. That is not a defect — the filter did what it was
configured to do — but it has a cost nobody has ever measured, because
rsi_crossover is the ONE filter with no toggle. Every other gate follows
cfg.get("use_X", ...); this one is unconditional, so it has never been
replayed with and without.

The gate is also the strictest thing in the stack. It demands RSI cross
its SMA on the EXACT entry bar (curr > sma AND prev <= sma) — a one-bar
event — in conjunction with trend, close-above-EMA, ATR regime, MACD,
ADX and the BTC filter all aligning on that same bar.

So the useful comparison is three-armed, not on/off:

  crossover  curr > sma AND prev <= sma AND in range   (today's behaviour)
  range      in range only — momentum confirmed, timing not demanded
  off        no RSI gate at all

Default stays "crossover", so nothing changes live until a replay earns
it. The point is to make the question answerable, not to answer it here.

Run: python -m pytest tests/test_momentum_rsi_mode.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pytest

pd = pytest.importorskip("pandas")

from signals import rsi_gate_ok


def _cfg(mode=None, lo=40, hi=70):
    c = {"rsi_min": lo, "rsi_max": hi}
    if mode is not None:
        c["rsi_mode"] = mode
    return c


# curr_rsi, prev_rsi, curr_sma, prev_sma
_CROSSED_IN_RANGE = (55.0, 45.0, 50.0, 50.0)   # crossed up, inside band
_ALREADY_ABOVE    = (55.0, 52.0, 50.0, 50.0)   # above, no cross this bar
_BELOW            = (45.0, 44.0, 50.0, 50.0)   # under its SMA
_CROSSED_OUT      = (85.0, 45.0, 50.0, 50.0)   # crossed but overbought


# ─── Default is unchanged ────────────────────────────────────────────────

def test_default_mode_is_todays_behaviour():
    assert rsi_gate_ok(*_CROSSED_IN_RANGE, _cfg()) is True
    assert rsi_gate_ok(*_ALREADY_ABOVE, _cfg()) is False


def test_an_unknown_mode_falls_back_to_crossover():
    """A typo in config must not silently loosen a live filter."""
    assert rsi_gate_ok(*_ALREADY_ABOVE, _cfg("crossver")) is False


# ─── crossover ───────────────────────────────────────────────────────────

def test_crossover_requires_the_cross_on_this_bar():
    assert rsi_gate_ok(*_CROSSED_IN_RANGE, _cfg("crossover")) is True
    assert rsi_gate_ok(*_ALREADY_ABOVE, _cfg("crossover")) is False


def test_crossover_still_respects_the_range():
    assert rsi_gate_ok(*_CROSSED_OUT, _cfg("crossover")) is False


# ─── range ───────────────────────────────────────────────────────────────

def test_range_admits_a_bar_that_already_crossed():
    """The arm that would have caught a rally already underway."""
    assert rsi_gate_ok(*_ALREADY_ABOVE, _cfg("range")) is True


def test_range_still_rejects_outside_the_band():
    assert rsi_gate_ok(*_CROSSED_OUT, _cfg("range")) is False


def test_range_does_not_require_being_above_the_sma():
    """Deliberate: 'range' is a band check, not a weaker crossover. RSI
    45 inside a 40-70 band qualifies."""
    assert rsi_gate_ok(*_BELOW, _cfg("range")) is True


# ─── off ─────────────────────────────────────────────────────────────────

def test_off_admits_everything():
    for args in (_CROSSED_IN_RANGE, _ALREADY_ABOVE, _BELOW, _CROSSED_OUT):
        assert rsi_gate_ok(*args, _cfg("off")) is True


# ─── Degradation ─────────────────────────────────────────────────────────

def test_nan_inputs_block_rather_than_admit():
    """A missing indicator must never open a gate."""
    nan = float("nan")
    assert rsi_gate_ok(nan, 45.0, 50.0, 50.0, _cfg("crossover")) is False
    assert rsi_gate_ok(nan, 45.0, 50.0, 50.0, _cfg("range")) is False


def test_off_still_admits_when_indicators_are_missing():
    """'off' means the gate is absent, so there is nothing to fail."""
    nan = float("nan")
    assert rsi_gate_ok(nan, nan, nan, nan, _cfg("off")) is True


# ─── The SHORT mirror shares the same gate ───────────────────────────────

def _scfg(mode=None, lo=30, hi=50):
    c = {"rsi_min": 40, "rsi_max": 70,
         "rsi_min_short": lo, "rsi_max_short": hi}
    if mode is not None:
        c["rsi_mode"] = mode
    return c


def test_short_default_requires_a_cross_down():
    # crossed DOWN through the SMA, inside the inverted band
    assert rsi_gate_ok(45.0, 55.0, 50.0, 50.0, _scfg(), short=True) is True
    # already below, no cross this bar
    assert rsi_gate_ok(45.0, 46.0, 50.0, 50.0, _scfg(), short=True) is False


def test_short_uses_the_inverted_band():
    """rsi_min_short/rsi_max_short, not the long band."""
    assert rsi_gate_ok(65.0, 70.0, 68.0, 68.0, _scfg(), short=True) is False


def test_short_range_mode_drops_the_timing_requirement():
    assert rsi_gate_ok(45.0, 46.0, 50.0, 50.0,
                        _scfg("range"), short=True) is True


def test_short_off_admits_everything():
    assert rsi_gate_ok(99.0, 1.0, 50.0, 50.0,
                        _scfg("off"), short=True) is True


def test_short_nan_blocks():
    nan = float("nan")
    assert rsi_gate_ok(nan, 55.0, 50.0, 50.0, _scfg(), short=True) is False
