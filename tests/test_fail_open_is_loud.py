"""Fail-open is a choice; failing open in silence is a bug (Aug 22 2026).

Today's defects all shared one shape: correct-ish degradation wrapped in
a handler that said nothing. append_forming_bar returned a stale frame
for three weeks because a NameError went into a bare except.

An audit of broad silent handlers found 58. Most are correct — the
watchdog philosophy is deliberate, a broken dashboard panel must not
take down the build. The dangerous subset is the handlers that fail
OPEN: they permit an action they were meant to gate, and say nothing.

Two were in the live decision path:

  _sleeve_blocked      returns False (= not blocked, TRADE) if the
                       kill-switch check raises
  ema200_alignment_ok  returns True (= filter passed) on any exception

Failing open is defensible for both — a broken breaker should not halt
the fleet, and a missing indicator should not permanently mute a
strategy. Failing open in silence is not: you can run a broken kill
switch for weeks and never learn.

These tests do not change the fail-open behaviour. They require it to
announce itself.

Run: python -m pytest tests/test_fail_open_is_loud.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pytest

pd = pytest.importorskip("pandas")

import signals
import stock_daily_main as sdm


# ─── The kill-switch check ───────────────────────────────────────────────

def test_a_broken_kill_switch_check_still_permits_trading(monkeypatch):
    """Deliberate: a breaker that cannot be read must not halt the
    fleet. Pinned so nobody 'fixes' it into a trading outage."""
    def boom(owner):
        raise RuntimeError("journal exploded")
    monkeypatch.setattr(sdm, "should_pause", boom)
    assert sdm._sleeve_blocked("rev") is False


def test_a_broken_kill_switch_check_says_so(monkeypatch, caplog):
    """...but it must never be silent. This is the difference between a
    degraded system and an invisible one."""
    def boom(owner):
        raise RuntimeError("journal exploded")
    monkeypatch.setattr(sdm, "should_pause", boom)
    with caplog.at_level("WARNING"):
        sdm._sleeve_blocked("rev")
    assert any("kill" in r.message.lower() or "journal exploded" in r.message
                for r in caplog.records), \
        "the kill-switch check failed open without a word"


def test_an_unapproved_sleeve_is_still_blocked_before_any_of_this():
    """The S2 gate comes first and does not depend on the breaker."""
    assert sdm._sleeve_blocked("trend") is True


# ─── The EMA200 filter ───────────────────────────────────────────────────

def _df(n=250, close=100.0, ema=90.0):
    return pd.DataFrame({"close": [close] * n, "ema200": [ema] * n})


def test_the_filter_works_normally():
    assert signals.ema200_alignment_ok(_df(), "LONG") is True
    assert signals.ema200_alignment_ok(_df(close=80.0), "LONG") is False


def test_missing_data_still_passes_the_filter():
    """Graceful degradation: a missing indicator must not permanently
    mute a strategy."""
    assert signals.ema200_alignment_ok(None, "LONG") is True
    assert signals.ema200_alignment_ok(_df(n=10), "LONG") is True


def test_an_unexpected_failure_passes_but_reports(caplog):
    """A degraded filter is fine. A filter degrading in silence is how
    you discover months later that a gate was never running."""
    class _Exploding:
        columns = ["ema200", "close"]
        def __len__(self):
            return 250
        def __getitem__(self, k):
            raise RuntimeError("frame is broken")
    with caplog.at_level("WARNING"):
        assert signals.ema200_alignment_ok(_Exploding(), "LONG") is True
    assert caplog.records, "the filter failed open without a word"
