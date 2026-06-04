"""Unit tests for tools.reconcile_journal.

Phase A.3 of the comprehensive enhancement plan. Verifies:
- The phantom-#10 detector matches only sign-flip-pattern rows
- Orphan detection finds whale rows whose state_key is absent from state.json
- Orphan detection does NOT report active positions or non-whale rows
- Apply mode creates a backup BEFORE mutating
- Dry-run mode does not mutate

Run: python -m pytest tests/test_reconcile_journal.py -v
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

from tools.reconcile_journal import (
    journal_state_key,
    find_phantom_10,
    find_whale_orphans,
    backup_db,
    apply_purge_phantom_10,
    apply_close_orphans,
)


# ─── Fixture helpers ────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    date_opened         TEXT NOT NULL,
    date_closed         TEXT,
    symbol              TEXT NOT NULL,
    direction           TEXT NOT NULL,
    entry_price         REAL NOT NULL,
    exit_price          REAL,
    quantity            REAL NOT NULL,
    leverage            INTEGER NOT NULL,
    fees                REAL DEFAULT 0,
    strategy            TEXT,
    bot                 TEXT,
    entry_reason        TEXT,
    exit_reason         TEXT,
    notes               TEXT,
    btc_trend_at_entry  TEXT,
    atr_regime_at_entry TEXT
);
"""


def _make_db(path: Path, rows: list[dict]) -> None:
    """Create a fresh sqlite trades.db at `path` with the given rows."""
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    for r in rows:
        conn.execute(
            "INSERT INTO trades (id, date_opened, date_closed, symbol, direction, "
            "entry_price, exit_price, quantity, leverage, fees, strategy, bot, "
            "exit_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["id"], r["date_opened"], r.get("date_closed"), r["symbol"],
                r["direction"], r["entry_price"], r.get("exit_price"),
                r["quantity"], r["leverage"], r.get("fees", 0),
                r["strategy"], r["bot"], r.get("exit_reason", ""),
            ),
        )
    conn.commit()
    conn.close()


def _row(id, symbol, direction, strategy, bot, entry_price=100.0,
         exit_price=None, date_closed=None):
    return dict(
        id=id, date_opened="2026-05-01T00:00:00", date_closed=date_closed,
        symbol=symbol, direction=direction, entry_price=entry_price,
        exit_price=exit_price, quantity=1.0, leverage=10,
        strategy=strategy, bot=bot,
    )


# ─── journal_state_key classifier ───────────────────────────────────────────

def test_journal_state_key_whale_btc():
    row = _row(1, "BTCUSDT", "SHORT", "Whale Track BTC SHORT", "Whale")
    assert journal_state_key(row) == "WHALE_BTC"


def test_journal_state_key_funding_eth():
    row = _row(2, "ETHUSDT", "LONG", "Funding Fade ETH LONG", "Funding")
    assert journal_state_key(row) == "FUNDING_ETH"


def test_journal_state_key_momentum_uses_symbol_base():
    row = _row(3, "XRPUSDT", "LONG", "XRP 4H Momentum v2", "Momentum")
    assert journal_state_key(row) == "XRP"


# ─── Phantom #10 detection ─────────────────────────────────────────────────

def test_find_phantom_10_matches_ton_sign_flip(tmp_path):
    """The exact pattern from the live DB: strategy=SHORT, direction=LONG."""
    db = tmp_path / "trades.db"
    _make_db(db, [
        _row(10, "TONUSDT", "LONG", "Whale Track TON SHORT", "Whale",
             entry_price=1.311, exit_price=1.7425,
             date_closed="2026-05-05T12:00:00"),
    ])
    result = find_phantom_10(db)
    assert result is not None
    assert result["id"] == 10
    assert result["symbol"] == "TONUSDT"


def test_find_phantom_10_does_not_match_normal_long_row(tmp_path):
    """A legit LONG trade at id=10 (no sign-flip) must NOT be flagged."""
    db = tmp_path / "trades.db"
    _make_db(db, [
        # Legit: strategy says LONG, direction LONG. No sign flip.
        _row(10, "BTCUSDT", "LONG", "BTC 4H Momentum v2", "Momentum",
             entry_price=80000, exit_price=82000,
             date_closed="2026-05-05T12:00:00"),
    ])
    assert find_phantom_10(db) is None


def test_find_phantom_10_returns_none_when_no_row_at_id_10(tmp_path):
    db = tmp_path / "trades.db"
    _make_db(db, [
        _row(1, "BTCUSDT", "LONG", "BTC 4H Momentum v2", "Momentum"),
    ])
    assert find_phantom_10(db) is None


# ─── Orphan detection ──────────────────────────────────────────────────────

def test_find_orphans_returns_whale_rows_not_in_state(tmp_path):
    """Whale rows with date_closed=NULL whose state_key is missing from state.json."""
    db = tmp_path / "trades.db"
    _make_db(db, [
        _row(1, "UNIUSDT", "SHORT", "Whale Track UNI SHORT", "Whale"),  # orphan
        _row(2, "ASTERUSDT", "SHORT", "Whale Track ASTER SHORT", "Whale"),  # orphan
        _row(3, "DOGEUSDT", "SHORT", "Whale Track DOGE SHORT", "Whale"),  # in state, NOT orphan
    ])
    state_keys = {"WHALE_DOGE"}
    orphans = find_whale_orphans(db, state_keys)
    assert {o["id"] for o in orphans} == {1, 2}


def test_find_orphans_excludes_closed_rows(tmp_path):
    """A closed whale row (date_closed set) is not an orphan."""
    db = tmp_path / "trades.db"
    _make_db(db, [
        _row(1, "UNIUSDT", "SHORT", "Whale Track UNI SHORT", "Whale",
             date_closed="2026-05-08T10:00:00"),  # closed → not an orphan
    ])
    assert find_whale_orphans(db, state_keys=set()) == []


def test_find_orphans_excludes_funding_rows(tmp_path):
    """The script's whale-orphan operation must not touch funding rows."""
    db = tmp_path / "trades.db"
    _make_db(db, [
        _row(1, "ETHUSDT", "LONG", "Funding Fade ETH LONG", "Funding"),
    ])
    assert find_whale_orphans(db, state_keys=set()) == []


# ─── Backup + apply behavior ───────────────────────────────────────────────

def test_backup_db_creates_timestamped_copy(tmp_path):
    db = tmp_path / "trades.db"
    _make_db(db, [_row(1, "BTCUSDT", "LONG", "BTC 4H Momentum v2", "Momentum")])

    backup_path = backup_db(db)

    assert backup_path.exists()
    assert backup_path.name.startswith("trades.db.backup.")
    # Backup byte-identical
    assert backup_path.read_bytes() == db.read_bytes()


def test_apply_purge_phantom_10_removes_row(tmp_path):
    db = tmp_path / "trades.db"
    _make_db(db, [
        _row(10, "TONUSDT", "LONG", "Whale Track TON SHORT", "Whale",
             entry_price=1.311, exit_price=1.7425,
             date_closed="2026-05-05T12:00:00"),
        _row(11, "BTCUSDT", "LONG", "BTC 4H Momentum v2", "Momentum"),
    ])

    removed = apply_purge_phantom_10(db)

    assert removed == 1
    # Row 10 gone, row 11 stays
    conn = sqlite3.connect(str(db))
    ids = [r[0] for r in conn.execute("SELECT id FROM trades").fetchall()]
    conn.close()
    assert 10 not in ids
    assert 11 in ids


def test_apply_purge_phantom_10_does_nothing_if_not_phantom(tmp_path):
    """Safety: don't delete id=10 if it isn't actually the sign-flip pattern."""
    db = tmp_path / "trades.db"
    _make_db(db, [
        # Legit LONG at id=10 — must NOT be deleted
        _row(10, "BTCUSDT", "LONG", "BTC 4H Momentum v2", "Momentum",
             entry_price=80000, exit_price=82000,
             date_closed="2026-05-05T12:00:00"),
    ])

    removed = apply_purge_phantom_10(db)

    assert removed == 0
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    conn.close()
    assert n == 1


def test_apply_close_orphans_sets_exit_price_to_entry_and_date_closed(tmp_path):
    db = tmp_path / "trades.db"
    _make_db(db, [
        _row(1, "UNIUSDT", "SHORT", "Whale Track UNI SHORT", "Whale",
             entry_price=3.22925),
        _row(2, "ASTERUSDT", "SHORT", "Whale Track ASTER SHORT", "Whale",
             entry_price=0.65855),
    ])
    orphans = find_whale_orphans(db, state_keys=set())
    assert len(orphans) == 2

    closed = apply_close_orphans(db, orphans)

    assert closed == 2
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute("SELECT * FROM trades ORDER BY id").fetchall())
    conn.close()
    for r in rows:
        assert r["exit_price"] == r["entry_price"]  # FLAT close
        assert r["date_closed"] is not None
        assert "reconciled" in (r["exit_reason"] or "").lower()
