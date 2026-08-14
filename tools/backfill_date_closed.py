"""Backfill date_closed on closed rows that were never stamped.

Aug 14 2026. main.py's three close paths (partial TP1, full exit,
rotation) never passed date_closed to log_trade, so every momentum trade
ever closed carries exit_price + exit_reason but a NULL close time.
Fixed at the write side in the same commit; this repairs history.

The exact close instant is NOT recoverable — nothing recorded it. What
IS recoverable is a bound: rows are INSERTed in close order under an
AUTOINCREMENT id, so an unstamped row closed at or after the newest
stamped row before it, and at or before the oldest stamped row after it.
We take the midpoint of that bracket and mark it estimated. When only a
lower neighbour exists we take it directly; when neither exists we fall
back to date_opened, which is a hard lower bound because a trade cannot
close before it opens.

Every estimate is tagged in `notes` so no future reader mistakes an
interpolation for a record.

Dry-run by default. --apply writes, and always backs up first.

  venv/bin/python tools/backfill_date_closed.py            # preview
  venv/bin/python tools/backfill_date_closed.py --apply
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

ESTIMATED_TAG = "[date_closed estimated by backfill]"


def _mid(a: str, b: str) -> str:
    da, db = datetime.fromisoformat(a), datetime.fromisoformat(b)
    if db < da:
        da, db = db, da
    return (da + (db - da) / 2).isoformat()


def plan_backfill(rows: list) -> list:
    """[(id, estimated_iso, basis)] for the rows needing a stamp.

    `rows` is every trade as {id, date_opened, date_closed, exit_price,
    strategy}, ordered by id ascending.
    """
    stamped = [(r["id"], r["date_closed"]) for r in rows if r.get("date_closed")]
    out = []
    for r in rows:
        if r.get("date_closed") or r.get("exit_price") is None:
            continue          # open row, or already stamped
        lower = max((ts for rid, ts in stamped if rid < r["id"]), default=None)
        upper = min((ts for rid, ts in stamped if rid > r["id"]), default=None)
        if lower and upper:
            est, basis = _mid(lower, upper), "bracketed by neighbours"
        elif lower:
            est, basis = lower, "lower neighbour"
        elif upper:
            est, basis = upper, "upper neighbour"
        elif r.get("date_opened"):
            est, basis = r["date_opened"], "date_opened (hard lower bound)"
        else:
            continue          # nothing to reason from; leave it alone
        # An estimate that precedes the open is impossible. Clamp rather
        # than write a contradiction into the journal.
        opened = r.get("date_opened")
        if opened and est < opened:
            est, basis = opened, "clamped to date_opened"
        out.append((r["id"], est, basis))
    return out


def _load(db: Path) -> list:
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        cur = c.execute(
            "SELECT id, date_opened, date_closed, exit_price, strategy, "
            "symbol, notes FROM trades ORDER BY id ASC")
        return [dict(r) for r in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=str(BOT_DIR / "trades.db"))
    args = ap.parse_args()
    db = Path(args.db)
    if not db.exists():
        print(f"no journal at {db}")
        return 1

    rows = _load(db)
    by_id = {r["id"]: r for r in rows}
    plan = plan_backfill(rows)
    if not plan:
        print("nothing to backfill - every closed row has a date_closed")
        return 0

    per_strategy: dict = {}
    for rid, _est, _b in plan:
        s = by_id[rid].get("strategy") or "?"
        per_strategy[s] = per_strategy.get(s, 0) + 1
    print(f"{len(plan)} closed rows are missing date_closed:")
    for s, n in sorted(per_strategy.items(), key=lambda kv: -kv[1]):
        print(f"  {n:4d}  {s}")
    print("\nfirst 10 estimates:")
    for rid, est, basis in plan[:10]:
        r = by_id[rid]
        sym = r.get("symbol") or "?"
        print(f"  id={rid:5d} {sym:10s} "
               f"opened={str(r.get('date_opened'))[:19]} -> "
               f"est={est[:19]}  ({basis})")

    if not args.apply:
        print(f"\nDRY RUN - {len(plan)} rows would be updated. "
               f"Re-run with --apply to write.")
        return 0

    backup = db.with_suffix(
        f".db.backup.{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(db, backup)
    print(f"\nbacked up -> {backup.name}")

    with sqlite3.connect(db) as c:
        for rid, est, basis in plan:
            note = (by_id[rid].get("notes") or "").strip()
            note = f"{note} {ESTIMATED_TAG} ({basis})".strip()
            c.execute("UPDATE trades SET date_closed=?, notes=? WHERE id=?",
                       (est, note, rid))
        c.commit()
    print(f"updated {len(plan)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
