"""Phase K — backtest Breakout candidates against the activation gates.

Run on the DROPLET (needs live WEEX kline access for each candidate).

Loops every `BREAKOUT_CANDIDATE_ASSETS` entry, calls
`replay_breakout` from tools/backtest_replay.py with 1000 bars, prints
PF / trades / max DD per asset, and applies the Phase K gates:

  - PF       >= 1.5
  - trades   >= 5      (over 1000 bars — relaxed from G.3's >=10 since
                          larger TFs print fewer bars naturally)
  - max DD   <= 15 %

Prints PASS / fail per asset. The operator then promotes passing rows
by moving them from BREAKOUT_CANDIDATE_ASSETS into BREAKOUT_ASSETS in
`crypto_bot/breakout_config.py`, optionally adding a matching row to
`BREAKOUT_BACKTEST_STATS` so the projection table updates.

Usage:
    cd /home/bot/crypto-bot
    venv/bin/python -m crypto_bot.tools.validate_breakout_candidates
    # or with custom bar count:
    venv/bin/python -m crypto_bot.tools.validate_breakout_candidates --bars 2000

This script never mutates state, journal, or config. Read-only.
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
                          help="Historical bars per asset (default 1000)")
    parser.add_argument("--asset", default=None,
                          help="Validate just one candidate asset name (e.g. BNB_4H)")
    args = parser.parse_args()

    try:
        from breakout_config import BREAKOUT_CANDIDATE_ASSETS
    except ImportError as e:
        print(f"  ERROR: cannot import BREAKOUT_CANDIDATE_ASSETS: {e}")
        return 2

    try:
        from tools.backtest_replay import replay_breakout
    except ImportError as e:
        print(f"  ERROR: cannot import replay_breakout: {e}")
        return 2

    candidates = BREAKOUT_CANDIDATE_ASSETS
    if args.asset:
        if args.asset not in candidates:
            print(f"  ERROR: {args.asset} not in BREAKOUT_CANDIDATE_ASSETS")
            print(f"  available: {', '.join(sorted(candidates.keys()))}")
            return 2
        candidates = {args.asset: candidates[args.asset]}

    print(f"\n=== BREAKOUT CANDIDATE VALIDATION ({args.bars} bars/asset) ===")
    print(f"Gates: PF >= {GATE_PF_MIN}, trades >= {GATE_TRADES_MIN}, "
            f"max DD <= {GATE_DD_MAX_PCT}%\n")

    passed: list[tuple[str, float, int, float]] = []
    failed: list[tuple[str, str]] = []

    for name, cfg in candidates.items():
        try:
            report = replay_breakout(asset_name=name, cfg=cfg, bars=args.bars)
        except Exception as e:  # noqa: BLE001
            print(f"  {name:10s}  ERROR: {e}")
            failed.append((name, f"replay error: {e}"))
            continue

        pf = report.profit_factor
        n  = report.n_trades
        dd = report.max_drawdown_pct
        verdict = _format_verdict(pf, n, dd)
        print(f"  {name:10s}  PF={pf:6.2f}  n={n:3d}  "
                f"maxDD={dd:5.1f}%  → {verdict}")

        if verdict == "PASS":
            passed.append((name, pf, n, dd))
        else:
            failed.append((name, verdict))

    print(f"\n=== SUMMARY ===")
    print(f"  PASS:  {len(passed)} / {len(candidates)}")
    for name, pf, n, dd in passed:
        print(f"    promote: {name}  (PF={pf:.2f}, n={n}, DD={dd:.1f}%)")
    if failed:
        print(f"  fail:  {len(failed)}")
        for name, why in failed:
            print(f"    skip:    {name}  ({why})")
    if passed:
        print(f"\nTo promote: move {len(passed)} row(s) from "
                f"BREAKOUT_CANDIDATE_ASSETS into BREAKOUT_ASSETS in "
                f"breakout_config.py, then add matching entries to "
                f"BREAKOUT_BACKTEST_STATS so the projection table updates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
