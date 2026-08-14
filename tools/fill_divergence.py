"""Signal price vs actual fill, per bot — the shadow cost model.

The S3 plan made this a first-class gate metric because Alpaca paper
matches against real NBBO but models no fees, no slippage, no market
impact and no queue position. It will therefore flatter every strategy
by roughly our entire cost model, and the only defence is measuring the
gap between the price a sleeve DECIDED on and the price it PAID.

First live equity entry was QQQ @ 730.70 on a 723.70 signal — 0.97% on
a sleeve whose edge is a small snap-back, and larger than the move it
was trying to capture. Two structural causes were fixed the same day
(decision bar a session too old; entries landing at the open rather than
the close). This tool measures what is LEFT, which is the number that
decides whether the S2 backtest describes live behaviour at all.

Reads fill_divergence_pct off the journal's notes/fields, so it works on
any bot that records it.

  venv/bin/python tools/fill_divergence.py --days 30
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))


def divergence_rows(trades: list, days: int, now=None) -> dict:
    """{bot: [pct, ...]} for trades inside the window that recorded one."""
    now = now or datetime.now()
    cutoff = (now - timedelta(days=days)).isoformat()
    out = defaultdict(list)
    for t in trades or []:
        when = t.get("date_closed") or t.get("date_opened") or ""
        if when < cutoff:
            continue
        pct = t.get("fill_divergence_pct")
        if pct is None:
            continue
        try:
            out[t.get("bot") or "?"].append(abs(float(pct)))
        except (TypeError, ValueError):
            continue
    return dict(out)


def summarize(pcts: list) -> dict:
    """Median leads, because one bad fill should not set the headline."""
    if not pcts:
        return {"n": 0}
    ordered = sorted(pcts)
    return {
        "n": len(ordered),
        "median": statistics.median(ordered),
        "mean": statistics.fmean(ordered),
        "p90": ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))],
        "worst": ordered[-1],
    }


def verdict(median_pct: float, gate_pct: float = 0.05) -> str:
    """The S3 gate is a median at or under 5 bps.

    Above it, the backtest's fill assumption is not reachable live and
    its profit factor is describing a strategy nobody can trade.
    """
    if median_pct <= gate_pct:
        return "PASS"
    if median_pct <= gate_pct * 4:
        return "WATCH"
    return "FAIL — backtest fills are not attainable live"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    from journal import read_trades
    rows = divergence_rows(read_trades(max_rows=10000), args.days)
    if not rows:
        print(f"no fill_divergence_pct recorded in the last {args.days}d.\n"
               "Only bots that persist it at entry appear here.")
        return 0

    print(f"=== FILL DIVERGENCE — last {args.days}d "
           f"(|signal - fill| as % of signal) ===\n")
    for bot in sorted(rows):
        s = summarize(rows[bot])
        print(f"  {bot:12s} n={s['n']:3d}  median={s['median']:.3f}%  "
               f"mean={s['mean']:.3f}%  p90={s['p90']:.3f}%  "
               f"worst={s['worst']:.3f}%")
        print(f"  {'':12s} -> {verdict(s['median'])}")
    print("\nGate: median <= 0.05%. Above ~0.20% the backtest's fill "
           "assumption is fiction and its PF cannot be trusted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
