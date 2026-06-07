"""Phase K — backtest Momentum candidates against the activation gates.

Run on the DROPLET (needs live WEEX kline access for each candidate).

Loops every `MOMENTUM_CANDIDATE_ASSETS` entry, calls
`replay_momentum` from tools/backtest_replay.py with 1000 bars (WEEX
caps `limit` at 1000), prints PF / trades / max DD per asset, and
applies the same gates the breakout flow uses:

  - PF       >= 1.5
  - trades   >= 5
  - max DD   <= 15 %

Prints PASS / fail per asset. The operator then promotes passing rows
by moving them from MOMENTUM_CANDIDATE_ASSETS into ASSETS in
`crypto_bot/config.py`, optionally adding a `backtest_stats` dict per
config so the projection table updates.

Usage:
    cd /home/bot/crypto-bot
    venv/bin/python tools/validate_momentum_candidates.py
    venv/bin/python tools/validate_momentum_candidates.py --asset BNB_4H
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))


GATE_PF_MIN     = 1.5
GATE_TRADES_MIN = 5
GATE_DD_MAX_PCT = 15.0

_INTERVAL_HOURS = {
    "1h": 1, "2h": 2, "4h": 4, "6h": 6, "8h": 8, "12h": 12,
    "1d": 24, "1w": 168,
}


def _interval_to_years(interval: str, bars: int) -> float:
    hours = _INTERVAL_HOURS.get(interval.lower())
    if hours is None:
        return 0.0
    return (bars * hours) / (365.25 * 24)


def _format_verdict(pf: float, n: int, dd: float) -> str:
    fails = []
    if pf < GATE_PF_MIN:
        fails.append(f"PF<{GATE_PF_MIN}")
    if n < GATE_TRADES_MIN:
        fails.append(f"n<{GATE_TRADES_MIN}")
    if dd > GATE_DD_MAX_PCT:
        fails.append(f"DD>{GATE_DD_MAX_PCT}%")
    return "PASS" if not fails else "fail (" + ", ".join(fails) + ")"


def main() -> int:
    logging.basicConfig(level=logging.WARNING,
                          format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--bars", type=int, default=1000,
                          help="Historical bars per asset (default 1000 — "
                                "WEEX caps kline limit at 1000)")
    parser.add_argument("--asset", default=None,
                          help="Validate just one candidate asset name (e.g. BNB_4H)")
    args = parser.parse_args()

    try:
        from config import MOMENTUM_CANDIDATE_ASSETS
    except ImportError as e:
        print(f"  ERROR: cannot import MOMENTUM_CANDIDATE_ASSETS: {e}")
        return 2

    try:
        from tools.backtest_replay import replay_momentum
    except ImportError as e:
        print(f"  ERROR: cannot import replay_momentum: {e}")
        return 2

    candidates = MOMENTUM_CANDIDATE_ASSETS
    if args.asset:
        if args.asset not in candidates:
            print(f"  ERROR: {args.asset} not in MOMENTUM_CANDIDATE_ASSETS")
            print(f"  available: {', '.join(sorted(candidates.keys()))}")
            return 2
        candidates = {args.asset: candidates[args.asset]}

    print(f"\n=== MOMENTUM CANDIDATE VALIDATION ({args.bars} bars/asset) ===")
    print(f"Gates: PF >= {GATE_PF_MIN}, trades >= {GATE_TRADES_MIN}, "
            f"max DD <= {GATE_DD_MAX_PCT}%\n")

    passed: list[tuple[str, float, int, float, float, float, float]] = []
    failed: list[tuple[str, str]] = []

    for name, cfg in candidates.items():
        try:
            report = replay_momentum(asset_name=name, cfg=cfg, bars=args.bars)
        except Exception as e:  # noqa: BLE001
            print(f"  {name:10s}  ERROR: {e}")
            failed.append((name, f"replay error: {e}"))
            continue

        pf      = report.profit_factor
        n       = report.n_trades
        dd      = report.max_drawdown_pct
        wr      = report.win_rate
        total   = report.total_return_pct
        years   = _interval_to_years(cfg.get("interval", ""), args.bars)
        verdict = _format_verdict(pf, n, dd)
        print(f"  {name:10s}  PF={pf:6.2f}  n={n:3d}  WR={wr:5.1f}%  "
                f"total={total:+6.1f}%  maxDD={dd:5.1f}%  "
                f"window={years:.2f}yr  → {verdict}")

        if verdict == "PASS":
            passed.append((name, pf, n, dd, wr, total, years))
        else:
            failed.append((name, verdict))

    print(f"\n=== SUMMARY ===")
    print(f"  PASS:  {len(passed)} / {len(candidates)}")
    for name, pf, n, dd, wr, total, years in passed:
        print(f"    promote: {name}  PF={pf:.2f}  n={n}  WR={wr:.1f}%  "
                f"total={total:+.1f}%  DD={dd:.1f}%  window={years:.2f}yr")
    if failed:
        print(f"  fail:  {len(failed)}")
        for name, why in failed:
            print(f"    skip:    {name}  ({why})")
    if passed:
        print(f"\nCopy-paste backtest_stats dict entries (drop into each "
                f"promoted ASSETS row):")
        for name, pf, n, dd, wr, total, years in passed:
            print(f'    {name}: "backtest_stats": '
                    f'{{"pf": {pf:.2f}, "trades": {n}, '
                    f'"pnl_pct": {total:.1f}, "dd_pct": {dd:.1f}}},')
    return 0


if __name__ == "__main__":
    sys.exit(main())
