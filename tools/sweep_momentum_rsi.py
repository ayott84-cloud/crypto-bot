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
        # Carried so pooling can sum real profits instead of
        # back-solving them out of n/wr/pf/ret, which is fragile
        # arithmetic when the report already knows the answer.
        "gp":  float(report.gross_profit),
        "gl":  float(report.gross_loss),
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


# ─── Pooled comparison (pre-registered Aug 22 2026, BEFORE the data) ─────
#
# The per-asset run came back INSUFFICIENT on all ten: no baseline
# reached 20 trades in 5000 bars. That is itself the finding — the
# crossover gate makes momentum genuinely low-frequency — but it leaves
# the arms uncompared.
#
# Pooling across assets gives ~140 baseline trades, which IS enough. The
# criteria below were written down before the pooled numbers were seen,
# because a threshold chosen after looking at the result is not a
# threshold.
#
# CRITERIA, fixed in advance:
#   * pooled baseline must reach n >= 100, else no verdict;
#   * a challenger must lift pooled PF by >= 0.15 (a bigger bar than the
#     per-asset 0.10: pooling narrows the error, so the effect should be
#     clearer, not blurrier);
#   * MEAN per-asset drawdown must not worsen by more than 2pp, and MAX
#     per-asset drawdown must not worsen by more than 5pp;
#   * a challenger must not lose on more assets than it wins on.
#
# PF and win rate pool honestly — they are ratios of summed profits and
# counts. DRAWDOWN DOES NOT. Each asset has its own equity curve, and a
# "pooled drawdown" would be a number describing no portfolio that ever
# existed. It is reported as mean and max across assets instead.

POOLED_MIN_N = 100
POOLED_MIN_PF_LIFT = 0.15
POOLED_MAX_MEAN_DD_COST = 2.0
POOLED_MAX_MAX_DD_COST = 5.0


def pool_arm(per_asset: list) -> dict:
    """Aggregate one arm across assets.

    `per_asset` is a list of summarize() dicts, one per asset.

    Pooled PF is a ratio of SUMMED gross profit and loss, not an average
    of per-asset profit factors — averaging ratios would weight a
    3-trade asset the same as a 40-trade one, which is how a thin asset
    with a lucky PF hijacks a fleet-wide conclusion.
    """
    n = wins = 0
    gp = gl = 0.0
    dds = []
    for r in per_asset or []:
        if not r or not r["n"]:
            continue
        n += r["n"]
        wins += round(r["n"] * r["wr"] / 100.0)
        gp += float(r.get("gp") or 0.0)
        gl += float(r.get("gl") or 0.0)
        dds.append(r["dd"])
    return {
        "n": n,
        "wr": round(wins / n * 100.0, 1) if n else 0.0,
        "pf": round(gp / gl, 2) if gl > 0 else 0.0,
        "dd_mean": round(sum(dds) / len(dds), 1) if dds else 0.0,
        "dd_max": round(max(dds), 1) if dds else 0.0,
        "assets": len(dds),
    }


def compare_pooled(pooled: dict, per_asset_pf: dict) -> str:
    """Verdict against the pre-registered criteria above.

    `per_asset_pf` is {mode: {asset: pf}} for the win/loss tally.
    """
    base = pooled.get("crossover")
    if not base or base["n"] < POOLED_MIN_N:
        got = base["n"] if base else 0
        return (f"INSUFFICIENT (pooled baseline n={got} "
                 f"< {POOLED_MIN_N})")
    best, verdicts = "crossover", []
    for mode in ("range", "off"):
        r = pooled.get(mode)
        if not r or r["n"] < POOLED_MIN_N:
            verdicts.append(f"{mode}: too few pooled trades")
            continue
        lift = r["pf"] - base["pf"]
        dd_mean_cost = r["dd_mean"] - base["dd_mean"]
        dd_max_cost = r["dd_max"] - base["dd_max"]
        base_pf = per_asset_pf.get("crossover", {})
        arm_pf = per_asset_pf.get(mode, {})
        better = sum(1 for a, pf in arm_pf.items()
                      if pf > base_pf.get(a, 0))
        worse = sum(1 for a, pf in arm_pf.items()
                     if pf < base_pf.get(a, 0))
        why = []
        if lift < POOLED_MIN_PF_LIFT:
            why.append(f"PF lift {lift:+.2f} < {POOLED_MIN_PF_LIFT}")
        if dd_mean_cost > POOLED_MAX_MEAN_DD_COST:
            why.append(f"mean DD +{dd_mean_cost:.1f}pp")
        if dd_max_cost > POOLED_MAX_MAX_DD_COST:
            why.append(f"max DD +{dd_max_cost:.1f}pp")
        if worse > better:
            why.append(f"worse on {worse} assets vs better on {better}")
        if why:
            verdicts.append(f"{mode}: REJECTED ({'; '.join(why)})")
        else:
            verdicts.append(f"{mode}: CANDIDATE (PF {r['pf']:.2f})")
            best = mode
    head = "KEEP crossover" if best == "crossover" else f"CANDIDATE {best}"
    return head + " — " + " | ".join(verdicts)


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
    by_arm = {m: [] for m in ARMS}          # pooled inputs
    pf_by_arm = {m: {} for m in ARMS}       # win/loss tally
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
            by_arm[mode].append(rows[mode])
            pf_by_arm[mode][name] = rows[mode]["pf"]
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
    pooled = {m: pool_arm(by_arm[m]) for m in ARMS}
    print()
    print("=== pooled across assets ===")
    print("PF and WR pool as ratios of sums. DRAWDOWN DOES NOT — each")
    print("asset has its own equity curve, so it is shown as mean/max")
    print("across assets rather than a portfolio number that never was.")
    for mode in ARMS:
        r = pooled[mode]
        print(f"  {mode:10s} n={r['n']:4d}  WR={r['wr']:5.1f}%  "
               f"PF={r['pf']:6.2f}  DD mean={r['dd_mean']:5.1f}% "
               f"max={r['dd_max']:5.1f}%  ({r['assets']} assets)")
    print()
    print(f"  -> {compare_pooled(pooled, pf_by_arm)}")
    print(f"  (criteria fixed in advance: n>={POOLED_MIN_N}, "
           f"PF lift >={POOLED_MIN_PF_LIFT}, mean DD cost "
           f"<={POOLED_MAX_MEAN_DD_COST}pp, max DD cost "
           f"<={POOLED_MAX_MAX_DD_COST}pp)")

    print()
    cands = [n for n, v in verdicts.items() if v.startswith("CANDIDATE")]
    print(f"\n{len(cands)} of {len(verdicts)} assets show a candidate arm.")
    print("Nothing here changes a live config. A candidate earns a config "
           "flip only per-asset, and only after the DD column is read as "
           "carefully as the PF column.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
