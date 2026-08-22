"""Tag trades that measure a defect rather than a strategy.

Aug 22 2026. StockRev's whole record is 15 QQQ round trips booked inside
90 minutes on Aug 14, when the sleeves re-decided a frozen daily bar
every poll. They report as n=15, WR 66.7%, PF 4.38, +$2.77 — the best
numbers in the fleet — and they measure a poll loop. Left alone they
dominate every 30-day window until mid-September, and the sleeve's real
shakedown cannot start until they stop counting.

WHAT THIS DOES NOT DO: it does not delete rows, and it does not touch a
price, a quantity or a PnL. Those fills happened. What is disputed is
whether they describe the strategy, and the answer is written onto the
row so anyone reading the journal later can disagree with it.

Excluding trades is the easiest way to flatter a track record, so:
  * every exclusion carries a reason, stored in `notes`;
  * the marker is plain text, greppable without this codebase;
  * fleet_review and live_vs_backtest REPORT what they excluded rather
    than quietly dropping it;
  * dry-run is the default and a backup precedes any write.

  venv/bin/python tools/exclude_trades.py --bot StockRev \\
      --on 2026-08-14 --exit-reason "Above SMA" \\
      --reason "churn loop: frozen-bar re-decision, fixed 7bed21e"
  # ...inspect, then re-run with --apply
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

from journal import EXCLUDED_PREFIX, excluded_reason  # noqa: E402


def select_rows(rows: list, bot=None, on=None, exit_reason=None,
                  symbol=None) -> list:
    """Rows matching every supplied criterion, skipping already-tagged.

    All criteria are AND-ed. At least one must be given by the CLI — a
    bare invocation that matched the whole journal would be a footgun.
    """
    out = []
    for r in rows:
        if excluded_reason(r) is not None:
            continue                              # already set aside
        if bot and (r.get("bot") or "") != bot:
            continue
        if symbol and (r.get("symbol") or "") != symbol:
            continue
        if exit_reason and (r.get("exit_reason") or "") != exit_reason:
            continue
        if on:
            stamp = str(r.get("date_closed") or r.get("date_opened") or "")
            if not stamp.startswith(on):
                continue
        out.append(r)
    return out


def tagged_notes(existing, reason: str) -> str:
    """Append the marker without discarding whatever was already there."""
    base = (existing or "").strip()
    tag = f"{EXCLUDED_PREFIX} {reason}]"
    return f"{base} {tag}".strip()


def _load(db: Path) -> list:
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        cur = c.execute(
            "SELECT id, date_opened, date_closed, symbol, bot, strategy, "
            "exit_reason, notes, exit_price FROM trades ORDER BY id ASC")
        return [dict(r) for r in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot")
    ap.add_argument("--symbol")
    ap.add_argument("--on", help="YYYY-MM-DD prefix on the close date")
    ap.add_argument("--exit-reason")
    ap.add_argument("--reason", required=True,
                     help="why these rows do not describe the strategy")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=str(BOT_DIR / "trades.db"))
    args = ap.parse_args()

    if not any((args.bot, args.symbol, args.on, args.exit_reason)):
        print("refusing to run with no selection criteria — that would "
               "match the entire journal")
        return 2

    db = Path(args.db)
    if not db.exists():
        print(f"no journal at {db}")
        return 1

    rows = _load(db)
    hits = select_rows(rows, bot=args.bot, on=args.on,
                        exit_reason=args.exit_reason, symbol=args.symbol)
    if not hits:
        print("no matching rows (already tagged rows are skipped)")
        return 0

    print(f"{len(hits)} rows would be tagged EXCLUDED:")
    print(f"  reason: {args.reason}\n")
    for r in hits[:20]:
        print(f"  id={r['id']:5d} {str(r.get('symbol')):8s} "
               f"{str(r.get('date_closed'))[:19]:19s} "
               f"{str(r.get('exit_reason'))}")
    if len(hits) > 20:
        print(f"  ... +{len(hits) - 20} more")

    print("\nPnL is NOT modified. These rows stay in the journal and stay "
           "greppable; they simply stop counting toward strategy stats.")

    if not args.apply:
        print(f"\nDRY RUN - re-run with --apply to write.")
        return 0

    backup = db.with_suffix(
        f".db.backup.{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(db, backup)
    print(f"\nbacked up -> {backup.name}")

    with sqlite3.connect(db) as c:
        for r in hits:
            c.execute("UPDATE trades SET notes=? WHERE id=?",
                       (tagged_notes(r.get("notes"), args.reason), r["id"]))
        c.commit()
    print(f"tagged {len(hits)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
