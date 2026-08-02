"""Module 2 Phase S4 — the sentinel must not page for a closed market.

The stock daemon deliberately sleeps toward the next open instead of
polling a shut market. That means its heartbeat is legitimately hours
old overnight and all weekend — and the sentinel's 30-minute bar would
fire an hourly Discord alert the whole time.

We have been here twice. Funding's hourly cycle tripped the same bar
(and cost a pointless restart), and the parked-bot relics before that.
The lesson each time: a staleness rule has to know the CADENCE it is
judging. For stocks the cadence is the market itself.

The escape hatch that exists today — marking a bot step-0 PARKED —
is not acceptable here: it also disables the genuine parked-alive
alarm, so we would be trading blind to stay quiet.

Run: python -m pytest tests/test_sentinel_market_hours.py -v
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest


def _hb(tmp_path, name, age_s):
    p = tmp_path / name
    p.touch()
    old = time.time() - age_s
    os.utime(p, (old, old))
    return p


@pytest.fixture(autouse=True)
def _no_parked(monkeypatch):
    import tools.risk_check as rc
    monkeypatch.setattr(rc, "_parked_owners", lambda: set())


# ─── Closed market: hours-old is healthy ─────────────────────────────────

def test_stock_heartbeat_not_stale_overnight(tmp_path, monkeypatch):
    import tools.risk_check as rc
    monkeypatch.setattr(rc, "_market_is_open", lambda: False)
    rows = rc.classify_heartbeats([_hb(tmp_path, ".stock_heartbeat", 4 * 3600)])
    assert rows[0]["stale"] is False


def test_stock_heartbeat_stale_if_it_outlives_the_sleep_cap(tmp_path,
                                                              monkeypatch):
    """The daemon caps its closed-market sleep at 4h precisely so it
    keeps beating. Beyond that it really is dead, weekend or not."""
    import tools.risk_check as rc
    monkeypatch.setattr(rc, "_market_is_open", lambda: False)
    rows = rc.classify_heartbeats([_hb(tmp_path, ".stock_heartbeat", 9 * 3600)])
    assert rows[0]["stale"] is True


# ─── Open market: the normal bar applies ─────────────────────────────────

def test_stock_heartbeat_stale_during_session(tmp_path, monkeypatch):
    """Open market, 5-minute poll — 40 minutes silent is a wedge."""
    import tools.risk_check as rc
    monkeypatch.setattr(rc, "_market_is_open", lambda: True)
    monkeypatch.setattr(rc, "_market_open_recently", lambda: False)
    rows = rc.classify_heartbeats([_hb(tmp_path, ".stock_heartbeat", 40 * 60)])
    assert rows[0]["stale"] is True


def test_grace_period_just_after_the_opening_bell(tmp_path, monkeypatch):
    """At 09:31 the daemon may still be finishing a capped overnight
    sleep. Alerting in that window would page every single morning."""
    import tools.risk_check as rc
    monkeypatch.setattr(rc, "_market_is_open", lambda: True)
    monkeypatch.setattr(rc, "_market_open_recently", lambda: True)
    rows = rc.classify_heartbeats([_hb(tmp_path, ".stock_heartbeat", 3 * 3600)])
    assert rows[0]["stale"] is False


# ─── Crypto owners are untouched ─────────────────────────────────────────

def test_crypto_owners_keep_the_24_7_rule(tmp_path, monkeypatch):
    import tools.risk_check as rc
    monkeypatch.setattr(rc, "_market_is_open", lambda: False)
    rows = rc.classify_heartbeats([
        _hb(tmp_path, ".scalp_heartbeat", 40 * 60),
        _hb(tmp_path, ".breakout_heartbeat", 5 * 60),
    ])
    by = {r["name"]: r for r in rows}
    assert by[".scalp_heartbeat"]["stale"] is True, \
        "a crypto bot got a market-hours pass"
    assert by[".breakout_heartbeat"]["stale"] is False


def test_funding_override_still_applies(tmp_path, monkeypatch):
    """The Jul 16 fix (hourly cycle, 150-min bar) must survive."""
    import tools.risk_check as rc
    monkeypatch.setattr(rc, "_market_is_open", lambda: False)
    rows = rc.classify_heartbeats([_hb(tmp_path, ".funding_heartbeat", 40 * 60)])
    assert rows[0]["stale"] is False


def test_market_hours_owners_are_declared_not_guessed():
    import tools.risk_check as rc
    assert "stock" in rc._MARKET_HOURS_OWNERS
    assert "scalp" not in rc._MARKET_HOURS_OWNERS
