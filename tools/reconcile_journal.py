"""Journal reconciliation script.

Phase A.3 of the comprehensive enhancement plan.

Three operations, each independently toggleable:

  --purge-phantom-10   Delete the sign-flipped TON rotation row at id=10
                       (only if the row matches the expected pattern:
                       strategy="Whale Track TON SHORT" AND direction="LONG").
  --close-orphans      For each whale-bot row with date_closed IS NULL whose
                       computed state_key is NOT in state.json's positions,
                       close it as FLAT (exit_price = entry_price) and tag
                       exit_reason="reconciled by tools/reconcile_journal.py".
  --vacuum             Run SQLite VACUUM to reclaim space after mutations.

Default mode is --dry-run (prints what would change, exits 0). Pass --apply
to actually mutate. --apply ALWAYS creates trades.db.backup.{timestamp} first.

Usage:
    # Inspect what would change
    python -m tools.reconcile_journal --close-orphans
    python -m tools.reconcile_journal --purge-phantom-10 --close-orphans --vacuum

    # Actually apply (after reviewing dry-run output)
    python -m tools.reconcile_journal --close-orphans --apply
    python -m tools.reconcile_journal --purge-phantom-10 --close-orphans --vacuum --apply
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("reconcile_journal")


# ─── State-key classifier ──────────────────────────────────────────────────

def journal_state_key(row: dict) -> str:
    """Compute the state.json position key a journal row maps to.

    Mirror of position_manager._bot_of_key but driven from the journal's
    `bot` column instead of an opaque state key. Symbol→coin via strip "USDT".
    """
    bot = row.get("bot") or ""
    symbol = row.get("symbol") or ""
    coin = symbol[:-4] if symbol.endswith("USDT") else symbol
    if bot == "Whale":
        return f"WHALE_{coin}"
    if bot == "Funding":
        return f"FUNDING_{coin}"
    return coin


# ─── Detectors (read-only) ─────────────────────────────────────────────────

def find_phantom_10(db_path: Path) -> Optional[dict]:
    """Return the id=10 row if it matches the sign-flip pattern, else None.

    Pattern: strategy contains "SHORT" but direction == "LONG" (the rotation-
    close bug logged the position as a LONG using long-math PnL on what was
    actually a SHORT position). Conservative — won't delete a legit id=10.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM trades WHERE id = 10").fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    d = dict(row)
    strategy = (d.get("strategy") or "").upper()
    direction = (d.get("direction") or "").upper()
    if "SHORT" in strategy and direction == "LONG":
        return d
    if "LONG" in strategy and direction == "SHORT":
        return d
    return None


def find_whale_orphans(db_path: Path, state_keys: set) -> list[dict]:
    """Whale entry-rows (exit_price IS NULL) whose state_key isn't in state.json.

    Important: the filter is `exit_price IS NULL`, NOT `date_closed IS NULL`.
    Pre-fix whale_main.close_whale_position did not pass date_closed to
    log_trade, so legitimately-closed whale rows have date_closed=NULL but
    exit_price=set. Filtering on date_closed would wrongly catch those as
    orphans (producing duplicate FLAT closes in the journal).

    These were opened (logged) but never had their close written — either the
    bot crashed mid-close, or the pre-A.2 close path failed at log_trade and
    rolled the position out of state without journaling. They pollute the
    open-trade count and per-bot stats.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM trades WHERE exit_price IS NULL AND bot = 'Whale'"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows if journal_state_key(dict(r)) not in state_keys]


def find_rows_by_ids(db_path: Path, ids: list[int]) -> list[dict]:
    """Full rows for the given ids (missing ids silently absent) — used to
    print exactly what --delete-ids would remove before --apply."""
    if not ids:
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT * FROM trades WHERE id IN ({marks}) ORDER BY id",
            [int(i) for i in ids]).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─── Mutators (only call after backup_db) ──────────────────────────────────

def backup_db(db_path: Path) -> Path:
    """Copy trades.db to trades.db.backup.{utc_iso} — call before any apply."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.name}.backup.{ts}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def apply_purge_phantom_10(db_path: Path) -> int:
    """Delete id=10 IFF it matches the sign-flip pattern. Returns rows removed."""
    if find_phantom_10(db_path) is None:
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("DELETE FROM trades WHERE id = 10")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def apply_close_orphans(db_path: Path, orphans: list[dict]) -> int:
    """Close each orphan FLAT (exit_price = entry_price). Returns count."""
    if not orphans:
        return 0
    now_iso = datetime.now(timezone.utc).isoformat()
    reason = "reconciled by tools/reconcile_journal.py"
    conn = sqlite3.connect(str(db_path))
    try:
        for o in orphans:
            conn.execute(
                "UPDATE trades SET exit_price = entry_price, date_closed = ?, "
                "exit_reason = ? WHERE id = ?",
                (now_iso, reason, o["id"]),
            )
        conn.commit()
        return len(orphans)
    finally:
        conn.close()


def apply_delete_ids(db_path: Path, ids: list[int]) -> int:
    """Delete exactly the listed row ids. Returns rows removed. Only call
    after backup_db (the CLI enforces this)."""
    if not ids:
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        marks = ",".join("?" * len(ids))
        cur = conn.execute(
            f"DELETE FROM trades WHERE id IN ({marks})",
            [int(i) for i in ids])
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def vacuum_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()


# ─── CLI ───────────────────────────────────────────────────────────────────

def _load_state_keys(state_path: Path) -> set:
    if not state_path.exists():
        logger.warning("state.json not found at %s — treating all open journal "
                       "rows as orphans (this is correct if the bot has been "
                       "rebooted into an empty state).", state_path)
        return set()
    with open(state_path, "r", encoding="utf-8") as f:
        state = json.load(f)
    return set((state.get("positions") or {}).keys())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile trades.db with state.json.")
    parser.add_argument("--db", type=Path, default=Path("trades.db"),
                        help="Path to trades.db (default: ./trades.db)")
    parser.add_argument("--state", type=Path, default=Path("state.json"),
                        help="Path to state.json (default: ./state.json)")
    parser.add_argument("--purge-phantom-10", action="store_true",
                        help="Delete the sign-flipped TON rotation row at id=10")
    parser.add_argument("--close-orphans", action="store_true",
                        help="Close whale rows in journal but absent from state.json")
    parser.add_argument("--vacuum", action="store_true", help="Run SQLite VACUUM")
    parser.add_argument("--delete-ids", type=str, default="",
                        help="Comma-separated row ids to delete (e.g. '412,413'). "
                             "Dry-run prints the full rows first; --apply removes "
                             "them after a backup.")
    parser.add_argument("--apply", action="store_true",
                        help="Actually mutate. Default is dry-run (no changes).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if not args.db.exists():
        logger.error("DB not found: %s", args.db)
        return 1

    if not (args.purge_phantom_10 or args.close_orphans or args.vacuum
            or args.delete_ids):
        parser.error("Specify at least one of: --purge-phantom-10, "
                     "--close-orphans, --vacuum, --delete-ids")

    delete_ids = [int(x) for x in args.delete_ids.split(",") if x.strip()]

    state_keys = _load_state_keys(args.state) if args.close_orphans else set()

    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"DB:   {args.db}")
    print(f"State:{args.state}  ({len(state_keys)} positions)\n")

    # Detect what would change
    phantom = find_phantom_10(args.db) if args.purge_phantom_10 else None
    orphans = find_whale_orphans(args.db, state_keys) if args.close_orphans else []

    if args.purge_phantom_10:
        if phantom:
            ep = phantom.get("exit_price") or 0.0
            qty = phantom.get("quantity") or 0.0
            net_long_math = (ep - phantom["entry_price"]) * qty
            print(f"PHANTOM #10: id={phantom['id']} {phantom['symbol']} "
                  f"strategy='{phantom['strategy']}' direction={phantom['direction']} "
                  f"entry={phantom['entry_price']} exit={ep} "
                  f"net (LONG-math)=${net_long_math:.2f}")
        else:
            print("PHANTOM #10: no matching row at id=10 (nothing to do).")

    if args.close_orphans:
        if orphans:
            print(f"ORPHAN whale rows ({len(orphans)}):")
            for o in orphans:
                print(f"  id={o['id']:3d} {o['symbol']:14s} "
                      f"{o['direction']:5s} entry=${o['entry_price']:.4f} "
                      f"opened={o['date_opened']}")
        else:
            print("ORPHAN whale rows: none.")

    if delete_ids:
        doomed = find_rows_by_ids(args.db, delete_ids)
        missing = set(delete_ids) - {r["id"] for r in doomed}
        print(f"DELETE-IDS ({len(doomed)} matching rows):")
        for r in doomed:
            print(f"  id={r['id']:4d} {r.get('symbol', '?'):12s} "
                  f"{r.get('direction', '?'):5s} strategy='{r.get('strategy', '')}' "
                  f"entry={r.get('entry_price')} exit={r.get('exit_price')} "
                  f"opened={r.get('date_opened')}")
        if missing:
            print(f"  (ids not found, skipped: {sorted(missing)})")

    if not args.apply:
        print("\nDry-run only. Pass --apply to actually mutate.")
        return 0

    # APPLY — backup first, then mutate
    backup_path = backup_db(args.db)
    print(f"\nBackup written: {backup_path.name}")

    if args.purge_phantom_10 and phantom is not None:
        n = apply_purge_phantom_10(args.db)
        print(f"Purged phantom rows: {n}")

    if args.close_orphans and orphans:
        n = apply_close_orphans(args.db, orphans)
        print(f"Closed orphans: {n}")

    if delete_ids:
        n = apply_delete_ids(args.db, delete_ids)
        print(f"Deleted rows: {n}")

    if args.vacuum:
        vacuum_db(args.db)
        print("VACUUM complete.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
