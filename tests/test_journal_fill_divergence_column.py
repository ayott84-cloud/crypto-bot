"""Aug 14 2026 — the S3 gate metric never reached the journal.

stock_daily_main computes fill_divergence_pct at entry and hands it to
register_entry, which stores it on the position in state.json. When the
position closes, log_trade writes the journal row — and it had no such
parameter and the schema no such column. The value died with the
position.

Found by running tools/fill_divergence.py against a live journal that
had just recorded a 0.97% divergence and getting "no fill_divergence_pct
recorded in the last 30d".

Same shape as the whale funnel and the equity fetcher: the measurement
was correct, and nothing that reads it could see it. A metric the S3
plan called first-class was in practice write-only.

CREATE TABLE IF NOT EXISTS cannot add a column to a table that already
exists, so the live droplet journal needs a guarded ALTER.

Run: python -m pytest tests/test_journal_fill_divergence_column.py -v
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pytest

pytest.importorskip("pandas")

import journal


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(journal, "_initialized", False)
    return tmp_path / "t.db"


def _cols(path) -> set:
    with sqlite3.connect(path) as c:
        return {r[1] for r in c.execute("PRAGMA table_info(trades)")}


# ─── The column exists and round-trips ───────────────────────────────────

def test_schema_carries_the_column(db):
    journal.log_trade(symbol="QQQ", direction="LONG", entry_price=730.70,
                       exit_price=730.99, quantity=2, leverage=1,
                       strategy="QQQ 1D StockRev")
    assert "fill_divergence_pct" in _cols(db)


def test_the_value_round_trips(db):
    journal.log_trade(symbol="QQQ", direction="LONG", entry_price=730.70,
                       exit_price=730.99, quantity=2, leverage=1,
                       strategy="QQQ 1D StockRev",
                       date_closed=datetime.now(timezone.utc),
                       fill_divergence_pct=0.967)
    row = journal.read_trades(max_rows=5)[0]
    assert row["fill_divergence_pct"] == pytest.approx(0.967)


def test_absent_divergence_stays_null_not_zero(db):
    """Zero would claim a perfect fill for every bot that never measured
    one — the exact misreading tools/fill_divergence.py guards against."""
    journal.log_trade(symbol="BTCUSDT", direction="LONG", entry_price=1.0,
                       exit_price=2.0, quantity=1, leverage=10,
                       strategy="BTC 4H Momentum v2")
    assert journal.read_trades(max_rows=5)[0]["fill_divergence_pct"] is None


# ─── Migration onto an existing journal ──────────────────────────────────

def test_an_existing_table_without_the_column_is_migrated(tmp_path,
                                                            monkeypatch):
    """The droplet journal already exists, and CREATE TABLE IF NOT EXISTS
    is a no-op against it — without an ALTER the column never appears."""
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as c:
        c.execute("""CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_opened TEXT NOT NULL, date_closed TEXT,
            symbol TEXT NOT NULL, direction TEXT NOT NULL,
            entry_price REAL NOT NULL, exit_price REAL,
            quantity REAL NOT NULL, leverage INTEGER NOT NULL,
            fees REAL DEFAULT 0, strategy TEXT, bot TEXT,
            entry_reason TEXT, exit_reason TEXT, notes TEXT,
            btc_trend_at_entry TEXT, atr_regime_at_entry TEXT)""")
        c.execute("INSERT INTO trades (date_opened, symbol, direction, "
                   "entry_price, quantity, leverage) "
                   "VALUES ('2026-08-01T00:00:00','SPY','LONG',1.0,1,1)")
    monkeypatch.setattr(journal, "DB_PATH", path)
    monkeypatch.setattr(journal, "_initialized", False)

    journal.log_trade(symbol="QQQ", direction="LONG", entry_price=730.70,
                       exit_price=730.99, quantity=2, leverage=1,
                       strategy="QQQ 1D StockRev", fill_divergence_pct=0.5)
    assert "fill_divergence_pct" in _cols(path)


def test_migration_preserves_existing_rows(tmp_path, monkeypatch):
    path = tmp_path / "legacy2.db"
    with sqlite3.connect(path) as c:
        c.execute("""CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_opened TEXT NOT NULL, date_closed TEXT,
            symbol TEXT NOT NULL, direction TEXT NOT NULL,
            entry_price REAL NOT NULL, exit_price REAL,
            quantity REAL NOT NULL, leverage INTEGER NOT NULL,
            fees REAL DEFAULT 0, strategy TEXT, bot TEXT,
            entry_reason TEXT, exit_reason TEXT, notes TEXT,
            btc_trend_at_entry TEXT, atr_regime_at_entry TEXT)""")
        c.execute("INSERT INTO trades (date_opened, symbol, direction, "
                   "entry_price, exit_price, quantity, leverage) "
                   "VALUES ('2026-08-01T00:00:00','SPY','LONG',1.0,2.0,1,1)")
    monkeypatch.setattr(journal, "DB_PATH", path)
    monkeypatch.setattr(journal, "_initialized", False)
    rows = journal.read_trades(max_rows=10)
    assert len(rows) == 1 and rows[0]["symbol"] == "SPY"


def test_migration_is_idempotent(db):
    """Adding a column that already exists raises in sqlite; running the
    daemon twice must not take the journal down."""
    journal.log_trade(symbol="QQQ", direction="LONG", entry_price=1.0,
                       exit_price=2.0, quantity=1, leverage=1, strategy="X")
    journal._initialized = False
    journal.log_trade(symbol="QQQ", direction="LONG", entry_price=1.0,
                       exit_price=2.0, quantity=1, leverage=1, strategy="X")
    assert len(journal.read_trades(max_rows=10)) == 2


# ─── The close path actually passes it ───────────────────────────────────

def test_stock_close_forwards_the_divergence():
    import inspect
    import stock_daily_main as sdm
    src = inspect.getsource(sdm.close_stock_position)
    assert "fill_divergence_pct" in src, \
        "the entry measures it and the close still drops it"
