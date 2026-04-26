"""Trade journal — JSONL append-only.

Replaced the Excel-backed journal in Apr 2026: the dashboard's export modal
covers the "give me a CSV" use case directly, and an Excel file with formula
columns was a poor fit for a Linux droplet (no Excel runtime, file locks,
formula recomputation needs Excel itself to open the file).

The new format is one JSON object per line. Append is atomic on Linux for
small writes (POSIX guarantees no interleaving for writes < PIPE_BUF, and
we're well under that). No file locking needed because both bots only
ever append; they never rewrite earlier lines.

Trade records are stored RAW (entry/exit/qty/fees) and PnL is computed
on read in the dashboard. This keeps the journal trivially correctable
(edit a line, fix a typo) without recomputing everything.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional

from config import JOURNAL_FILE

logger = logging.getLogger("crypto_bot.journal")


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
) -> bool:
    """Append a trade record to trades.jsonl. Returns True on success.

    Both open-only (entry_price set, exit_price=None) and closed
    (both set) records are supported; the dashboard distinguishes by
    presence of exit_price.
    """
    record = {
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "quantity": float(quantity) if quantity is not None else 0.0,
        "leverage": int(leverage) if leverage else 1,
        "fees": float(fees) if fees is not None else 0.0,
        "strategy": strategy,
        "entry_reason": entry_reason,
        "exit_reason": exit_reason,
        "notes": notes,
        "date_opened": (date_opened or datetime.now()).isoformat(),
        "date_closed": (date_closed.isoformat() if date_closed else None),
    }
    try:
        # 'a' mode opens for append; each write is atomic for short payloads.
        with open(JOURNAL_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        logger.info("Logged trade: %s %s %s @ %s -> %s",
                    direction, symbol, strategy,
                    entry_price, exit_price or "(open)")
        return True
    except (IOError, OSError) as e:
        logger.error("Failed to log trade to %s: %s", JOURNAL_FILE, e)
        return False


def read_trades(max_rows: int = 5000) -> List[dict]:
    """Read trade records from the JSONL file. Returns a list of dicts.

    Computed fields added per trade for dashboard compatibility:
        gross_pnl: (exit - entry) * qty * direction-sign
        net_pnl:   gross - fees
        result:    "WIN" / "LOSS" / "FLAT" / "OPEN"
    """
    if not JOURNAL_FILE.exists():
        return []
    out: List[dict] = []
    try:
        with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line)
                except json.JSONDecodeError as e:
                    logger.warning("Skipping malformed journal line: %s", e)
                    continue
                _enrich_pnl(t)
                out.append(t)
                if len(out) >= max_rows:
                    break
    except (IOError, OSError) as e:
        logger.error("Failed to read trades from %s: %s", JOURNAL_FILE, e)
    return out


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
    """No-op kept for back-compat with main.py.

    The old Excel backend queued writes that failed because the file was
    locked by Excel; the JSONL backend never blocks so there's nothing
    to flush.
    """
    return 0
