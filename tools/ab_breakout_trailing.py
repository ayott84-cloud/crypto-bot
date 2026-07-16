"""Per-asset trailing-exit A/B for the breakout bot (Step-2 research).

Every live breakout asset runs the offset-armed trailing exit (arms at
1.5xATR favorable, trails 1.0xATR off the water mark). This harness
replays each asset's SAME long window through four exit-stack arms:

  live       — as configured (trailing ON, arm 1.5 / trail 1.0)
  no_trail   — use_trailing_exit=False (Donchian/ADX/SL stack only)
  wide_trail — trail 2.0xATR (protect runners longer)
  early_arm  — arm 1.0xATR (start protecting sooner)

The window is fetched ONCE per asset and copied per arm, so arms differ
only in the exit stack — same conservative fills, same P2.2 round-trip
costs. A switch recommendation requires the challenger to beat live by
PF >= +0.10 with n >= 5 (the Phase M/N sweep pass criterion); anything
less is noise and the verdict is KEEP live.

Run (droplet): venv/bin/python tools/ab_breakout_trailing.py \
                   --bars 17000 --source binance [--assets BTC_4H,ETH_4H]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

PF_EPSILON = 0.10   # challenger must beat live PF by at least this
MIN_TRADES = 5      # arms below this sample size don't compete


def variant_cfgs(cfg: dict) -> dict:
    """The four A/B arms as independent cfg copies (input untouched)."""
    return {
        "live":       dict(cfg),
        "no_trail":   {**cfg, "use_trailing_exit": False},
        "wide_trail": {**cfg, "use_trailing_exit": True,
                        "trail_atr_mult": 2.0},
        "early_arm":  {**cfg, "use_trailing_exit": True,
                        "trail_arm_atr_mult": 1.0},
    }


def verdict(rows: dict) -> str:
    """rows: {arm: {pf, n, dd}} -> 'KEEP live' | 'SWITCH -> arm' |
    'INSUFFICIENT n'. Ties on PF break toward lower drawdown."""
    eligible = {k: v for k, v in rows.items() if v.get("n", 0) >= MIN_TRADES}
    if "live" not in eligible:
        return "INSUFFICIENT n"
    best = max(eligible.items(),
                key=lambda kv: (kv[1]["pf"], -kv[1].get("dd", 0.0)))
    name, stats = best
    if name == "live" or stats["pf"] - eligible["live"]["pf"] < PF_EPSILON:
        return "KEEP live"
    return f"SWITCH -> {name}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bars", type=int, default=17000)
    ap.add_argument("--source", choices=["weex", "binance"], default="binance")
    ap.add_argument("--assets", type=str, default="",
                     help="Comma-separated asset keys (default: all live "
                          "BREAKOUT_ASSETS)")
    args = ap.parse_args()

    from breakout_config import BREAKOUT_ASSETS
    from tools.backtest_replay import (
        replay_breakout, _fetch_klines, _filter_universe,
    )

    universe = _filter_universe(BREAKOUT_ASSETS, args.assets or None)
    if not universe:
        print(f"No assets matched {args.assets!r} in BREAKOUT_ASSETS "
               f"({', '.join(BREAKOUT_ASSETS)})")
        return 1

    print(f"=== BREAKOUT TRAILING-EXIT A/B — {args.bars} bars/asset, "
           f"source={args.source} ===")
    print(f"    switch rule: challenger PF >= live PF + {PF_EPSILON}, "
           f"n >= {MIN_TRADES}\n")

    for name, cfg in universe.items():
        try:
            df = _fetch_klines(cfg["symbol"], cfg["interval"], args.bars,
                                source=args.source)
        except Exception as e:  # noqa: BLE001
            print(f"-- {name}: fetch failed: {e}\n")
            continue
        print(f"-- {name} ({len(df)} bars) --")
        rows = {}
        for arm, arm_cfg in variant_cfgs(cfg).items():
            try:
                rep = replay_breakout(name, arm_cfg, bars=args.bars,
                                        source=args.source,
                                        pre_fetched_df=df.copy())
            except Exception as e:  # noqa: BLE001
                print(f"  {arm:10s} replay failed: {e}")
                continue
            rows[arm] = {"pf": rep.profit_factor, "n": rep.n_trades,
                          "dd": rep.max_drawdown_pct}
            print(f"  {arm:10s} trades={rep.n_trades:4d}  "
                   f"WR={rep.win_rate:5.1f}%  PF={rep.profit_factor:5.2f}  "
                   f"total={rep.total_return_pct:+7.1f}%  "
                   f"maxDD={rep.max_drawdown_pct:5.1f}%  "
                   f"E[t]={rep.expectancy_pct:+5.2f}%")
        if rows:
            print(f"  VERDICT: {verdict(rows)}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
