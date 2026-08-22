"""What does momentum's RSI gate cost?

Aug 22 2026. BTC ran +18.7% over 30 days and the fleet lost money.
Momentum's blocker histogram says why: rsi_crossover held 8 of 10
configs flat while the trend gate was open. The filter did exactly what
it was configured to do — and its price had never been measured, because
it was the one gate in the stack with no toggle.

Three arms, replayed per asset over the long window:

  crossover  RSI crosses its SMA on THIS bar, inside the band (today)
  range      inside the band; momentum confirmed, timing not demanded
  off        no RSI gate

This does NOT change any live config. It produces the evidence a config
change would need, and prints what each arm costs in trades, win rate,
profit factor and drawdown. Loosening a filter almost always raises
trade count and lowers win rate; whether that is an improvement is a
profit-factor-and-drawdown question, not a trade-count one.

Read the DD column as hard as the PF column. An arm that lifts PF while
doubling drawdown has not won.

  venv/bin/python tools/sweep_momentum_rsi.py --bars 5000 --source coinbase
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

ARMS = ("crossover", "range", "off")


def arm_config(cfg: dict, mode: str) -> dict:
    """A copy of `cfg` with only the RSI gate strength changed.

    Copied, never mutated: sharing a dict across arms is how a sweep
    silently measures the same thing three times.
    """
    out = dict(cfg)
    out["rsi_mode"] = mode
    return out


def summarize(report) -> dict:
    """The four numbers an arm is judged on, plus its warnings."""
    if report is None:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "dd": 0.0, "ret": 0.0,
                "warnings": ["replay returned nothing"]}
    return {
        "n":   report.n_trades,
        "wr":  round(report.win_rate, 1),
        "pf":  round(report.profit_factor, 2),
        "dd":  round(report.max_drawdown_pct, 1),
        "ret": round(report.total_return_pct, 1),
        "warnings": list(getattr(report, "warnings", []) or []),
    }


def compare(rows: dict) -> str:
    """A verdict, or an honest refusal to give one.

    Refuses on small samples for the same reason tools/live_vs_backtest
    exists: a filter change that moves n from 4 to 9 has not been
    measured, it has been observed.
    """
    base = rows.get("crossover")
    if not base or base["n"] < 20:
        return "INSUFFICIENT (baseline n<20 — nothing to compare against)"
    best, best_pf = "crossover", base["pf"]
    for mode in ("range", "off"):
        r = rows.get(mode)
        if not r or r["n"] < 20:
            continue
        # Require a real PF lift AND no meaningful drawdown penalty.
        if r["pf"] >= best_pf + 0.10 and r["dd"] <= base["dd"] + 2.0:
            best, best_pf = mode, r["pf"]
    if best == "crossover":
        return "KEEP crossover"
    return f"CANDIDATE {best} (PF {best_pf:.2f} vs {base['pf']:.2f})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=5000)
    ap.add_argument("--source", default="coinbase")
    ap.add_argument("--assets", nargs="*",
                     help="default: every live momentum asset")
    args = ap.parse_args()

    from config import ASSETS
    from tools.backtest_replay import replay_momentum

    names = args.assets or list(ASSETS)
    print(f"=== momentum RSI gate sweep — {len(names)} assets, "
           f"{args.bars} bars, {args.source} ===")
    print("Loosening a gate raises trade count and usually lowers win "
           "rate. Judge on PF and DD together.\n")

    verdicts = {}
    for name in names:
        cfg = ASSETS[name]
        rows = {}
        for mode in ARMS:
            try:
                rep = replay_momentum(name, arm_config(cfg, mode),
                                       bars=args.bars, source=args.source)
            except Exception as e:  # noqa: BLE001
                print(f"  {name:12s} {mode:10s} FAILED: {e}")
                continue
            rows[mode] = summarize(rep)
        if not rows:
            continue
        print(f"{name}")
        for mode in ARMS:
            r = rows.get(mode)
            if not r:
                continue
            warn = "  ⚠ " + "; ".join(r["warnings"]) if r["warnings"] else ""
            print(f"  {mode:10s} n={r['n']:4d}  WR={r['wr']:5.1f}%  "
                   f"PF={r['pf']:6.2f}  DD={r['dd']:5.1f}%  "
                   f"ret={r['ret']:+8.1f}%{warn}")
        v = compare(rows)
        verdicts[name] = v
        print(f"  -> {v}\n")

    print("=== summary ===")
    for name, v in verdicts.items():
        print(f"  {name:12s} {v}")
    cands = [n for n, v in verdicts.items() if v.startswith("CANDIDATE")]
    print(f"\n{len(cands)} of {len(verdicts)} assets show a candidate arm.")
    print("Nothing here changes a live config. A candidate earns a config "
           "flip only per-asset, and only after the DD column is read as "
           "carefully as the PF column.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
