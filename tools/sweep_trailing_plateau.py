"""Parameter-plateau sweep for the Jul 16-17 trailing-exit flips.

Gate A (backtest-expert) requires a stable plateau across parameter
variation — a narrow optimum is curve-fitting wearing a lab coat. The
trailing A/B chose arm 1.0xATR / trail 1.0xATR for ETH_4H and SOL_4H
from three discrete arms; this sweep replays a +-20% grid (arm x trail
in {0.8, 1.0, 1.2}) on ONE fetched window per asset so cells differ
only in the two knobs.

Plateau rule: every cell with n >= MIN_TRADES must hold PF >= 1.0.
The chosen point being merely the best is fine; a neighbor collapsing
below breakeven means the edge lives on a knife's edge and the flip
should be reconsidered.

Run (droplet): venv/bin/python tools/sweep_trailing_plateau.py \
                   --bars 17000 --source binance [--assets ETH_4H,SOL_4H]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

STEPS = (0.8, 1.0, 1.2)
MIN_TRADES = 5


def grid_cfgs(cfg: dict, steps=STEPS) -> dict:
    """{(arm, trail): cfg-copy} for the full grid; input untouched."""
    return {
        (arm, trail): {**cfg, "use_trailing_exit": True,
                        "trail_arm_atr_mult": arm,
                        "trail_atr_mult": trail}
        for arm in steps for trail in steps
    }


def plateau_verdict(cells: dict, center=(1.0, 1.0)) -> dict:
    """cells: {(arm, trail): {pf, n}}. Cells under MIN_TRADES are noise
    and can neither pass nor fail the plateau."""
    eligible = {k: v for k, v in cells.items()
                 if v.get("n", 0) >= MIN_TRADES}
    center_pf = cells.get(center, {}).get("pf")
    if not eligible:
        return {"plateau": False, "center_pf": center_pf,
                 "min_pf": None, "max_pf": None, "worst_cell": None,
                 "note": "no eligible cells"}
    worst_cell, worst = min(eligible.items(), key=lambda kv: kv[1]["pf"])
    best = max(v["pf"] for v in eligible.values())
    return {
        "plateau": worst["pf"] >= 1.0,
        "center_pf": center_pf,
        "min_pf": worst["pf"],
        "max_pf": best,
        "worst_cell": worst_cell,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bars", type=int, default=17000)
    ap.add_argument("--source", choices=["weex", "binance"], default="binance")
    ap.add_argument("--assets", type=str, default="ETH_4H,SOL_4H",
                     help="Comma-separated BREAKOUT_ASSETS keys (default: "
                          "the two flipped 4h configs)")
    args = ap.parse_args()

    from breakout_config import BREAKOUT_ASSETS
    from tools.backtest_replay import (
        replay_breakout, _fetch_klines, _filter_universe,
    )

    universe = _filter_universe(BREAKOUT_ASSETS, args.assets or None)
    if not universe:
        print(f"No assets matched {args.assets!r}")
        return 1

    print(f"=== TRAILING PLATEAU SWEEP — arm x trail grid {STEPS}, "
           f"{args.bars} bars/asset, source={args.source} ===")
    print(f"    plateau rule: every cell with n >= {MIN_TRADES} holds "
           "PF >= 1.0\n")

    for name, cfg in universe.items():
        try:
            df = _fetch_klines(cfg["symbol"], cfg["interval"], args.bars,
                                source=args.source)
        except Exception as e:  # noqa: BLE001
            print(f"-- {name}: fetch failed: {e}\n")
            continue
        print(f"-- {name} ({len(df)} bars) --")
        print(f"  {'arm/trail':>9s}  " + "  ".join(f"{t:>10.1f}" for t in STEPS))
        cells = {}
        for arm in STEPS:
            row = []
            for trail in STEPS:
                cell_cfg = grid_cfgs(cfg)[(arm, trail)]
                try:
                    rep = replay_breakout(name, cell_cfg, bars=args.bars,
                                            source=args.source,
                                            pre_fetched_df=df.copy())
                    cells[(arm, trail)] = {"pf": rep.profit_factor,
                                            "n": rep.n_trades}
                    row.append(f"{rep.profit_factor:5.2f}/n{rep.n_trades:<3d}")
                except Exception as e:  # noqa: BLE001
                    row.append("ERR")
                    print(f"  cell ({arm},{trail}) failed: {e}")
            print(f"  {arm:>9.1f}  " + "  ".join(f"{c:>10s}" for c in row))
        v = plateau_verdict(cells)
        status = "PLATEAU ✓" if v["plateau"] else "KNIFE'S EDGE ✗"
        print(f"  VERDICT: {status}  center PF={v['center_pf']:.2f}  "
               f"grid min={v['min_pf']:.2f} (cell {v['worst_cell']})  "
               f"max={v['max_pf']:.2f}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
