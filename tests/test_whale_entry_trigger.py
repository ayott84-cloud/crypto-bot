"""Whale W.2.13 — price-action entry-trigger gate.

Structural fix from the Phase W.1 staleness diagnosis + course module-09
review: our whale bot entered on whale CONSENSUS alone, which is
structurally late (by the time top-20 leaderboard consensus forms, the
move is partially priced — 12/14 historical trades died by SL). The fix
inverts the roles: whale flow becomes CONTEXT, price action becomes the
TRIGGER. After every other filter passes, the entry waits until the
last completed 4h bar CONFIRMS the direction:

  LONG:  bar closed green  AND close broke above the prior bar's high
  SHORT: bar closed red    AND close broke below the prior bar's low

Missing data → pass (graceful degradation, same convention as every
other whale filter).

Run: python -m pytest tests/test_whale_entry_trigger.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))


# ─── check_entry_trigger — pure function in whale_filters ──────────────────

def test_long_passes_on_green_break_of_prior_high():
    from whale_filters import check_entry_trigger
    ok, reason = check_entry_trigger(
        "LONG", last_open=100.0, last_close=103.0,
        prior_high=102.0, prior_low=98.0)
    assert ok is True
    assert reason == ""


def test_long_blocked_when_bar_red():
    """Even above prior high, a red bar is distribution — no trigger."""
    from whale_filters import check_entry_trigger
    ok, reason = check_entry_trigger(
        "LONG", last_open=104.0, last_close=103.0,
        prior_high=102.0, prior_low=98.0)
    assert ok is False
    assert "trigger" in reason.lower()


def test_long_blocked_when_no_break_of_prior_high():
    from whale_filters import check_entry_trigger
    ok, reason = check_entry_trigger(
        "LONG", last_open=100.0, last_close=101.5,
        prior_high=102.0, prior_low=98.0)
    assert ok is False


def test_short_passes_on_red_break_of_prior_low():
    from whale_filters import check_entry_trigger
    ok, reason = check_entry_trigger(
        "SHORT", last_open=100.0, last_close=97.0,
        prior_high=102.0, prior_low=98.0)
    assert ok is True


def test_short_blocked_when_bar_green():
    from whale_filters import check_entry_trigger
    ok, _ = check_entry_trigger(
        "SHORT", last_open=96.0, last_close=97.5,
        prior_high=102.0, prior_low=98.0)
    assert ok is False


def test_short_blocked_when_no_break_of_prior_low():
    from whale_filters import check_entry_trigger
    ok, _ = check_entry_trigger(
        "SHORT", last_open=100.0, last_close=99.0,
        prior_high=102.0, prior_low=98.0)
    assert ok is False


def test_passes_on_missing_data():
    """None anywhere → pass (graceful degradation like every whale filter)."""
    from whale_filters import check_entry_trigger
    assert check_entry_trigger("LONG", None, 103.0, 102.0, 98.0)[0] is True
    assert check_entry_trigger("LONG", 100.0, None, 102.0, 98.0)[0] is True
    assert check_entry_trigger("LONG", 100.0, 103.0, None, 98.0)[0] is True
    assert check_entry_trigger("SHORT", 100.0, 97.0, 102.0, None)[0] is True


def test_unknown_direction_passes():
    from whale_filters import check_entry_trigger
    assert check_entry_trigger("", 100.0, 103.0, 102.0, 98.0)[0] is True


# ─── _evaluate_entry_trigger — klines adapter in whale_main ────────────────

def _klines(rows):
    """WEEX raw kline rows: [ts, open, high, low, close, volume, ...]."""
    return [[i, str(o), str(h), str(l), str(c), "1000"]
            for i, (o, h, l, c) in enumerate(rows)]


def test_adapter_long_trigger_fires_from_raw_klines():
    from whale_main import _evaluate_entry_trigger
    # rows: (open, high, low, close); last row = still-forming bar (ignored)
    rows = [
        (100, 101, 99, 100.5),   # older
        (100.5, 102, 100, 101),  # prior completed bar (high=102)
        (101, 104, 100.8, 103),  # LAST COMPLETED: green, close 103 > 102
        (103, 103.5, 102.5, 103.2),  # forming — must be ignored
    ]
    ok, reason = _evaluate_entry_trigger("LONG", _klines(rows))
    assert ok is True


def test_adapter_long_blocked_from_raw_klines():
    from whale_main import _evaluate_entry_trigger
    rows = [
        (100, 101, 99, 100.5),
        (100.5, 102, 100, 101),      # prior high = 102
        (101, 101.8, 100.2, 101.5),  # last completed: green but close < 102
        (101.5, 102, 101, 101.7),    # forming
    ]
    ok, reason = _evaluate_entry_trigger("LONG", _klines(rows))
    assert ok is False


def test_adapter_passes_on_short_klines():
    """Fewer than 3 rows → can't evaluate → pass."""
    from whale_main import _evaluate_entry_trigger
    ok, _ = _evaluate_entry_trigger("LONG", _klines([(100, 101, 99, 100.5)]))
    assert ok is True


def test_adapter_passes_on_garbage_klines():
    from whale_main import _evaluate_entry_trigger
    ok, _ = _evaluate_entry_trigger("LONG", [["x"], None, "bad"])
    assert ok is True
