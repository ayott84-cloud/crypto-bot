"""Jul 16 2026 fleet-review pollution — root-cause fixes.

The 14-day fleet review showed bot="Momentum" n=216 PF=0.21 with
"Time Stop" exits split BTCUSDT 108 / ETHUSDT 107. Diagnosis:

  1. pair_main tagged trades "Pair ETHBTC" (prefix form) while
     journal._bot_tag / kill_switch._bot_of classify via
     endswith("Pair") — so every pair leg journaled as Momentum and
     counted against momentum's kill-switch owner.
  2. pair_main incremented bars_held once per 5-min POLL cycle, not
     per daily bar — max_hold_bars=5 fired "Time Stop" after ~25 min,
     then re-entered while |z| >= 2 persisted: the thrash loop that
     produced ~8 round trips/day on a bot meant to hold 5 DAYS.
  3. tools/risk_check excluded PARKED owners' heartbeats entirely, so
     the still-running (supposedly parked) pair service was invisible
     to the hourly sentinel. A parked bot with a FRESH heartbeat is an
     incident, not noise.
  4. tools/reconcile_journal gains --delete-ids for the phantom
     breakout row (entry ~100.0 BTCUSDT closed at market).

Run: python -m pytest tests/test_pollution_fixes.py -v
"""

from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest


# ─── 1. Strategy-tag classification (both classifiers, both forms) ─────────

def test_bot_tag_classifies_legacy_pair_prefix_form():
    """Rows already written as 'Pair ETHBTC' must classify as Pair so
    retag_bot_column can repair them."""
    import journal
    assert journal._bot_tag("Pair ETHBTC") == "Pair"
    assert journal._bot_tag("Pair BTCLTC") == "Pair"


def test_bot_of_classifies_legacy_pair_prefix_form():
    import kill_switch
    assert kill_switch._bot_of("Pair ETHBTC") == "pair"
    assert kill_switch._bot_of("Pair BTCLTC") == "pair"


def test_pair_strategy_tag_is_suffix_form():
    """New rows use the suffix form every other bot uses ('BTC 5m Scalp',
    'ETH 1h Crossover'... 'ETHBTC Pair') so endswith classification holds."""
    import journal
    import kill_switch
    from pair_main import strategy_tag_for
    tag = strategy_tag_for("ETHBTC")
    assert tag == "ETHBTC Pair"
    assert journal._bot_tag(tag) == "Pair"
    assert kill_switch._bot_of(tag) == "pair"


def test_retag_repairs_prefix_form_rows(tmp_path):
    """retag_bot_column must flip the 216 polluted rows Momentum → Pair."""
    import journal
    db = tmp_path / "trades.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, strategy TEXT, bot TEXT)")
    conn.executemany("INSERT INTO trades (strategy, bot) VALUES (?, ?)", [
        ("Pair ETHBTC", "Momentum"),      # polluted — must become Pair
        ("BTC 1D Momentum", "Momentum"),  # correct — untouched
    ])
    conn.commit()
    conn.close()
    changed = journal.retag_bot_column(db_path=db, apply=True)
    assert changed == 1
    conn = sqlite3.connect(db)
    rows = dict(conn.execute("SELECT strategy, bot FROM trades"))
    conn.close()
    assert rows["Pair ETHBTC"] == "Pair"
    assert rows["BTC 1D Momentum"] == "Momentum"


# ─── 2. bars_held counts real interval bars, not poll cycles ───────────────

def _pos(minutes_ago: float | None, legacy_counter: int = 0) -> dict:
    pos = {"bars_held": legacy_counter}
    if minutes_ago is not None:
        pos["entry_time"] = (datetime.now(timezone.utc)
                              - timedelta(minutes=minutes_ago)).isoformat()
    return pos


def test_bars_held_zero_right_after_entry():
    from pair_main import _bars_held
    assert _bars_held(_pos(minutes_ago=1), "1d") == 0


def test_bars_held_zero_after_25_minutes_on_daily():
    """The exact thrash scenario: 5 poll cycles (25 min) must NOT count
    as 5 daily bars."""
    from pair_main import _bars_held
    assert _bars_held(_pos(minutes_ago=25), "1d") == 0


def test_bars_held_five_after_five_days_on_daily():
    from pair_main import _bars_held
    assert _bars_held(_pos(minutes_ago=5 * 24 * 60 + 90), "1d") == 5


def test_bars_held_counts_4h_bars():
    from pair_main import _bars_held
    assert _bars_held(_pos(minutes_ago=9 * 60), "4h") == 2


def test_bars_held_falls_back_to_legacy_counter():
    """Positions opened before this fix have no entry_time — fall back to
    the per-cycle counter (increment) rather than raising."""
    from pair_main import _bars_held
    assert _bars_held(_pos(minutes_ago=None, legacy_counter=3), "1d") == 4


# ─── 3. Sentinel: parked owner with a FRESH heartbeat is an incident ───────

def test_parked_bot_with_fresh_heartbeat_alerts(tmp_path, monkeypatch):
    """The pair service ran for 2 weeks while marked PARKED; the sentinel
    ignored its heartbeat entirely. Fresh beat + parked owner must flag."""
    import tools.risk_check as rc
    monkeypatch.setattr(rc, "_parked_owners", lambda: {"pair"})
    hb = tmp_path / ".pair_heartbeat"
    hb.touch()  # fresh — service is alive
    rows = rc.classify_heartbeats([hb], stale_after_s=1800)
    assert len(rows) == 1
    assert rows[0]["name"] == ".pair_heartbeat"
    assert rows[0].get("parked_alive") is True
    assert rows[0]["stale"] is False


def test_parked_bot_with_stale_heartbeat_still_ignored(tmp_path, monkeypatch):
    """A relic heartbeat of a genuinely stopped parked bot stays silent
    (the .reversal_heartbeat false-positive fix of Jul 5-6 holds)."""
    import os
    import tools.risk_check as rc
    monkeypatch.setattr(rc, "_parked_owners", lambda: {"reversal"})
    hb = tmp_path / ".reversal_heartbeat"
    hb.touch()
    old = time.time() - 90000
    os.utime(hb, (old, old))
    rows = rc.classify_heartbeats([hb], stale_after_s=1800)
    assert rows == []


def test_build_issues_flags_parked_alive():
    from tools.risk_check import build_issues
    issues = build_issues(
        ks_summary={},
        daily={"pnl": 0.0, "threshold": -150.0, "breached": False},
        heartbeats=[{"name": ".pair_heartbeat", "age_s": 60,
                      "stale": False, "parked_alive": True}],
        missing_sl=[],
    )
    assert any("PARKED" in i and ".pair_heartbeat" in i for i in issues)


# ─── 4. reconcile_journal --delete-ids ─────────────────────────────────────

def _mk_trades_db(tmp_path) -> Path:
    db = tmp_path / "trades.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE trades (
        id INTEGER PRIMARY KEY, symbol TEXT, direction TEXT,
        entry_price REAL, exit_price REAL, quantity REAL,
        strategy TEXT, bot TEXT, date_opened TEXT, date_closed TEXT)""")
    conn.executemany(
        "INSERT INTO trades (id, symbol, direction, entry_price, exit_price, "
        "quantity, strategy, bot, date_opened) VALUES (?,?,?,?,?,?,?,?,?)", [
            (1, "ETHUSDT", "LONG", 3000.0, 3050.0, 0.1, "ETH 5m Scalp", "Scalp", "2026-07-10"),
            (2, "BTCUSDT", "LONG", 100.0, 3231.11, 1.0, "BTC 4H Breakout", "Breakout", "2026-07-05"),
            (3, "BTCUSDT", "SHORT", 118000.0, 117500.0, 0.01, "BTC 4H Breakout", "Breakout", "2026-07-12"),
        ])
    conn.commit()
    conn.close()
    return db


def test_find_rows_by_ids_returns_full_rows(tmp_path):
    from tools.reconcile_journal import find_rows_by_ids
    db = _mk_trades_db(tmp_path)
    rows = find_rows_by_ids(db, [2, 99])
    assert len(rows) == 1
    assert rows[0]["id"] == 2
    assert rows[0]["entry_price"] == 100.0


def test_apply_delete_ids_removes_only_listed(tmp_path):
    from tools.reconcile_journal import apply_delete_ids
    db = _mk_trades_db(tmp_path)
    removed = apply_delete_ids(db, [2])
    assert removed == 1
    conn = sqlite3.connect(db)
    ids = [r[0] for r in conn.execute("SELECT id FROM trades ORDER BY id")]
    conn.close()
    assert ids == [1, 3]


def test_apply_delete_ids_empty_is_noop(tmp_path):
    from tools.reconcile_journal import apply_delete_ids
    db = _mk_trades_db(tmp_path)
    assert apply_delete_ids(db, []) == 0
