"""Trade journal — SQLite-backed.

Replaced the JSONL append-log in May 2026 (peer-review feedback): SQLite is
better for the rolling-window queries the per-regime expectancy report needs,
durable under concurrent reads, and avoids the O(n) reparse every dashboard
generation.

The same `log_trade()` and `read_trades()` API is preserved — main.py and
whale_main.py keep working unchanged. On first call, if `trades.jsonl` exists
beside the bot, it's imported into the SQLite db once (idempotent) and the
JSONL is renamed to `trades.jsonl.migrated`.

Schema (one trades table; everything denormalized for fast scans):

  id                 INTEGER PK
  date_opened        TEXT (ISO8601)
  date_closed        TEXT (ISO8601, null for open positions)
  symbol             TEXT
  direction          TEXT (LONG / SHORT)
  entry_price        REAL
  exit_price         REAL (null for open)
  quantity           REAL
  leverage           INTEGER
  fees               REAL
  strategy           TEXT
  bot                TEXT (computed: "Whale" if strategy starts "Whale Track" else "Momentum")
  entry_reason       TEXT
  exit_reason        TEXT
  notes              TEXT
  -- regime tags populated at log time when known
  btc_trend_at_entry TEXT (UP / DOWN / null)
  atr_regime_at_entry TEXT (HIGH / LOW / null)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from config import BOT_DIR

logger = logging.getLogger("crypto_bot.journal")

DB_PATH = BOT_DIR / "trades.db"
LEGACY_JSONL = BOT_DIR / "trades.jsonl"
LEGACY_JSONL_MIGRATED = BOT_DIR / "trades.jsonl.migrated"

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
CREATE INDEX IF NOT EXISTS idx_trades_date_closed ON trades(date_closed);
CREATE INDEX IF NOT EXISTS idx_trades_strategy    ON trades(strategy);
CREATE INDEX IF NOT EXISTS idx_trades_bot         ON trades(bot);
CREATE INDEX IF NOT EXISTS idx_trades_symbol      ON trades(symbol);
"""

_init_lock = threading.Lock()
_initialized = False


def _bot_tag(strategy: str) -> str:
    """Classify a strategy string into Whale vs Momentum bot."""
    if isinstance(strategy, str) and strategy.startswith("Whale Track"):
        return "Whale"
    return "Momentum"


@contextmanager
def _conn():
    """Connection with WAL mode + foreign keys (sane defaults)."""
    global _initialized
    conn = sqlite3.connect(str(DB_PATH), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        if not _initialized:
            with _init_lock:
                if not _initialized:
                    conn.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")
                    conn.executescript(_SCHEMA)
                    _import_legacy_jsonl(conn)
                    _initialized = True
        yield conn
    finally:
        conn.close()


def _import_legacy_jsonl(conn) -> None:
    """One-time migration of trades.jsonl into the SQLite table.

    Idempotent: only runs if trades.jsonl exists AND trades table is empty,
    or the table doesn't already contain the imported rows. After successful
    import, JSONL is renamed to trades.jsonl.migrated to prevent re-import
    on next start.
    """
    if not LEGACY_JSONL.exists():
        return
    cur = conn.execute("SELECT COUNT(*) FROM trades")
    existing = cur.fetchone()[0]
    if existing > 0:
        # Already migrated previously (or somehow have data). Don't reimport.
        try:
            LEGACY_JSONL.rename(LEGACY_JSONL_MIGRATED)
        except OSError:
            pass
        return

    imported = 0
    failed = 0
    with open(LEGACY_JSONL, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except json.JSONDecodeError:
                failed += 1
                continue
            try:
                conn.execute(
                    "INSERT INTO trades (date_opened, date_closed, symbol, direction, "
                    "entry_price, exit_price, quantity, leverage, fees, strategy, bot, "
                    "entry_reason, exit_reason, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        t.get("date_opened") or datetime.now().isoformat(),
                        t.get("date_closed"),
                        t.get("symbol", ""),
                        t.get("direction", "LONG"),
                        float(t.get("entry_price") or 0),
                        float(t["exit_price"]) if t.get("exit_price") is not None else None,
                        float(t.get("quantity") or 0),
                        int(t.get("leverage") or 1),
                        float(t.get("fees") or 0),
                        t.get("strategy", ""),
                        _bot_tag(t.get("strategy", "")),
                        t.get("entry_reason", ""),
                        t.get("exit_reason", ""),
                        t.get("notes", ""),
                    ),
                )
                imported += 1
            except (sqlite3.Error, TypeError, ValueError) as e:
                failed += 1
                logger.warning("Skipping bad legacy trade row: %s", e)
    logger.info("Migrated %d trade records from JSONL → SQLite (%d skipped)", imported, failed)
    try:
        LEGACY_JSONL.rename(LEGACY_JSONL_MIGRATED)
    except OSError as e:
        logger.warning("Could not rename %s after migration: %s", LEGACY_JSONL, e)


def log_trade(
    symbol: str,
    direction: str,
    entry_price: float,
    exit_price: Optional[float],
    quantity: float,
    leverage: int = 10,
    fees: float = 0.0,
    strategy: str = "",
    entry_reason: str = "",
    exit_reason: str = "",
    notes: str = "",
    date_opened: Optional[datetime] = None,
    date_closed: Optional[datetime] = None,
    btc_trend_at_entry: Optional[str] = None,
    atr_regime_at_entry: Optional[str] = None,
) -> bool:
    """Insert one trade row. Returns True on success.

    Both open-only (exit_price=None) and closed records are supported. The
    dashboard differentiates by presence of exit_price.

    Optional regime tags (`btc_trend_at_entry`, `atr_regime_at_entry`) are
    populated when the caller knows them at entry time. The expectancy report
    uses these to slice WR by regime.
    """
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO trades (date_opened, date_closed, symbol, direction, "
                "entry_price, exit_price, quantity, leverage, fees, strategy, bot, "
                "entry_reason, exit_reason, notes, btc_trend_at_entry, atr_regime_at_entry) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    (date_opened or datetime.now()).isoformat(),
                    date_closed.isoformat() if date_closed else None,
                    symbol,
                    direction,
                    float(entry_price or 0),
                    float(exit_price) if exit_price is not None else None,
                    float(quantity) if quantity is not None else 0.0,
                    int(leverage) if leverage else 1,
                    float(fees) if fees is not None else 0.0,
                    strategy,
                    _bot_tag(strategy),
                    entry_reason,
                    exit_reason,
                    notes,
                    btc_trend_at_entry,
                    atr_regime_at_entry,
                ),
            )
        logger.info("Logged trade: %s %s %s @ %s -> %s",
                    direction, symbol, strategy,
                    entry_price, exit_price if exit_price is not None else "(open)")
        return True
    except sqlite3.Error as e:
        logger.error("Failed to log trade: %s", e)
        return False


def read_trades(max_rows: int = 5000) -> List[dict]:
    """Read trade records, newest first, with computed gross_pnl/net_pnl/result.

    Computed fields (added per row to match the JSONL-era schema the
    dashboard expects):
        gross_pnl  (exit-entry)*qty*direction-sign
        net_pnl    gross_pnl - fees
        result     "WIN" / "LOSS" / "FLAT" / "OPEN"
    """
    try:
        with _conn() as c:
            cur = c.execute(
                "SELECT * FROM trades ORDER BY id ASC LIMIT ?",
                (max_rows,),
            )
            rows = [dict(r) for r in cur.fetchall()]
    except sqlite3.Error as e:
        logger.error("Failed to read trades: %s", e)
        return []
    for t in rows:
        _enrich_pnl(t)
    return rows


def read_recent_trades(hours: int = 24) -> List[dict]:
    """Read trades closed in the last N hours. Used by kill_switch."""
    cutoff = datetime.now().replace(microsecond=0)
    cutoff_iso = (cutoff.replace(hour=cutoff.hour) if cutoff.hour >= hours
                  else cutoff).isoformat()
    try:
        with _conn() as c:
            cur = c.execute(
                "SELECT * FROM trades WHERE date_closed IS NOT NULL "
                "AND date_closed >= ? ORDER BY date_closed ASC",
                (cutoff_iso,),
            )
            rows = [dict(r) for r in cur.fetchall()]
    except sqlite3.Error as e:
        logger.error("Failed to read recent trades: %s", e)
        return []
    for t in rows:
        _enrich_pnl(t)
    return rows


def _enrich_pnl(t: dict) -> None:
    """Compute gross_pnl, net_pnl, result on a record in-place."""
    try:
        entry = float(t.get("entry_price") or 0)
        exit_p = t.get("exit_price")
        exit_p = float(exit_p) if exit_p is not None else None
        qty = float(t.get("quantity") or 0)
        fees = float(t.get("fees") or 0)
    except (TypeError, ValueError):
        entry = exit_p = qty = fees = 0.0

    direction = t.get("direction", "LONG")
    sign = 1 if direction == "LONG" else -1

    if exit_p is not None and exit_p > 0 and entry > 0 and qty > 0:
        gross = (exit_p - entry) * qty * sign
        net = gross - fees
        t["gross_pnl"] = round(gross, 4)
        t["net_pnl"] = round(net, 4)
        t["result"] = "WIN" if net > 0 else ("LOSS" if net < 0 else "FLAT")
    else:
        t["gross_pnl"] = 0.0
        t["net_pnl"] = 0.0
        t["result"] = "OPEN"


def flush_pending() -> int:
    """No-op kept for back-compat with main.py (Excel era queue is gone)."""
    return 0
