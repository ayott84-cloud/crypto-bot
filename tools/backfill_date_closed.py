"""Backfill date_closed on closed rows that were never stamped.

Aug 14 2026. main.py's three close paths (partial TP1, full exit,
rotation) never passed date_closed to log_trade, so every momentum trade
ever closed carries exit_price + exit_reason but a NULL close time.
Fixed at the write side in the same commit; this repairs history.

The close instant IS recoverable for these rows, exactly.

journal.log_trade defaults date_opened to datetime.now() AT INSERT, and
no bot has ever passed date_opened explicitly. Momentum, and the legacy
whale close path, insert a row only when the position closes -- so on an
unstamped close row, date_opened is the close timestamp, to the second.
It was never an open time at all.

A first version of this tool ignored that and interpolated between the
id-ordered stamped neighbours. Against the real journal that put every
April and May close at 2026-06-05: five-week holds on a 4H strategy,
which is plainly false. Recorded data beats inference, so date_opened is
used directly, clamped only so a row cannot close after a later-inserted
row already had.

Interpolation survives as the fallback for a row with no date_opened at
all, and only those rows are tagged as estimates. Rows recovered from
the insert timestamp are labelled recovered, because that is what they
are -- mislabelling them would make the journal less trustworthy, not
more.

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
RECOVERED_TAG = "[date_closed recovered from insert timestamp]"


def _mid(a: str, b: str) -> str:
    da, db = datetime.fromisoformat(a), datetime.fromisoformat(b)
    if db < da:
        da, db = db, da
    return (da + (db - da) / 2).isoformat()


def is_estimate(basis: str) -> bool:
    """True when the value was inferred rather than read off a record."""
    return basis.startswith("estimated")


def plan_backfill(rows: list) -> list:
    """[(id, close_iso, basis)] for the rows needing a stamp.

    `rows` is every trade as {id, date_opened, date_closed, exit_price,
    strategy}, ordered by id ascending.
    """
    stamped = [(r["id"], r["date_closed"]) for r in rows if r.get("date_closed")]
    out = []
    for r in rows:
        if r.get("date_closed") or r.get("exit_price") is None:
            continue          # open row, or already stamped
        upper = min((ts for rid, ts in stamped if rid > r["id"]), default=None)

        opened = r.get("date_opened")
        if opened:
            # log_trade stamps date_opened at INSERT, and a close-only
            # insert means that instant IS the close. Recorded, not
            # inferred, so it wins over any interpolation.
            est, basis = opened, "insert time (close-only row)"
            if upper and est > upper:
                # A row inserted earlier cannot have closed after a row
                # inserted later. Trust the ordering over the clock.
                est, basis = upper, "clamped to next stamped close"
            out.append((r["id"], est, basis))
            continue

        # No date_opened at all: fall back to the id-ordered bracket.
        lower = max((ts for rid, ts in stamped if rid < r["id"]), default=None)
        if lower and upper:
            est, basis = _mid(lower, upper), "estimated: bracketed"
        elif lower:
            est, basis = lower, "estimated: lower neighbour"
        elif upper:
            est, basis = upper, "estimated: upper neighbour"
        else:
            continue          # nothing to reason from; leave it alone
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

    n_est = sum(1 for _r, _e, b in plan if is_estimate(b))
    print(f"\n  {len(plan) - n_est} recovered exactly from the insert "
           f"timestamp, {n_est} interpolated")

    print("\nfirst 10:")
    for rid, est, basis in plan[:10]:
        r = by_id[rid]
        sym = r.get("symbol") or "?"
        print(f"  id={rid:5d} {sym:10s} -> {est[:19]}  ({basis})")

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
            tag = ESTIMATED_TAG if is_estimate(basis) else RECOVERED_TAG
            note = f"{note} {tag} ({basis})".strip()
            c.execute("UPDATE trades SET date_closed=?, notes=? WHERE id=?",
                       (est, note, rid))
        c.commit()
    print(f"updated {len(plan)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
