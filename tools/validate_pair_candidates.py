"""Phase K — backtest Pair candidates against the activation gates.

Run on the DROPLET (needs live WEEX kline access for each leg).

Loops every `PAIR_CANDIDATE_CONFIGS` entry, calls `replay_pair` from
tools/backtest_replay.py with the candidate's symbols/cfg, prints
PF / trades / max DD per pair, and applies pair-specific gates:

  - PF       >= 1.3   (lower than directional bots — spreads have
                        narrower edge but higher signal frequency)
  - trades   >= 5
  - max DD   <= 20 %   (higher than directional — pair PnL is the
                        differential of two legs, naturally wider)

The currently-live ETHBTC pair was validated separately (PF=4.96,
n=42, 2.63yr window) — it's the reference these candidates compete
against.

Usage:
    cd /home/bot/crypto-bot
    venv/bin/python tools/validate_pair_candidates.py
    venv/bin/python tools/validate_pair_candidates.py --asset BTCSOL
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))


GATE_PF_MIN     = 1.3
# Phase L gate tightening — see validate_breakout_candidates for rationale
GATE_TRADES_MIN = 15

# Pair PnL is the differential of two legs — naturally wider DD than
# single-leg strategies. TF-scaled but with higher ceilings than
# breakout/momentum.
GATE_DD_MAX_BY_TF = {
    "4h":  18.0,
    "1d":  25.0,
    "1w":  32.0,
}
GATE_DD_DEFAULT = 25.0

_INTERVAL_HOURS = {
    "1h": 1, "4h": 4, "8h": 8, "12h": 12, "1d": 24, "1w": 168,
}


def _interval_to_years(interval: str, bars: int) -> float:
    h = _INTERVAL_HOURS.get(interval.lower())
    if h is None:
        return 0.0
    return (bars * h) / (365.25 * 24)


def _dd_gate(interval: str) -> float:
    return GATE_DD_MAX_BY_TF.get(interval.lower(), GATE_DD_DEFAULT)


def _format_verdict(pf: float, n: int, dd: float, dd_gate: float) -> str:
    fails = []
    if pf < GATE_PF_MIN:
        fails.append(f"PF<{GATE_PF_MIN}")
    if n < GATE_TRADES_MIN:
        fails.append(f"n<{GATE_TRADES_MIN}")
    if dd > dd_gate:
        fails.append(f"DD>{dd_gate:.0f}%")
    return "PASS" if not fails else "fail (" + ", ".join(fails) + ")"


def main() -> int:
    logging.basicConfig(level=logging.WARNING,
                          format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--bars", type=int, default=1000,
                          help="Historical bars per leg (default 1000 — "
                                "WEEX caps kline limit at 1000)")
    parser.add_argument("--asset", default=None,
                          help="Validate just one candidate pair name "
                                "(e.g. BTCSOL)")
    args = parser.parse_args()

    try:
        from pair_config import PAIR_CANDIDATE_CONFIGS
    except ImportError as e:
        print(f"  ERROR: cannot import PAIR_CANDIDATE_CONFIGS: {e}")
        return 2

    try:
        from tools.backtest_replay import replay_pair
    except ImportError as e:
        print(f"  ERROR: cannot import replay_pair: {e}")
        return 2

    candidates = PAIR_CANDIDATE_CONFIGS
    if args.asset:
        if args.asset not in candidates:
            print(f"  ERROR: {args.asset} not in PAIR_CANDIDATE_CONFIGS")
            print(f"  available: {', '.join(sorted(candidates.keys()))}")
            return 2
        candidates = {args.asset: candidates[args.asset]}

    print(f"\n=== PAIR CANDIDATE VALIDATION ({args.bars} bars/asset) ===")
    dd_summary = ", ".join(f"{tf}<={v:.0f}%"
                              for tf, v in GATE_DD_MAX_BY_TF.items())
    print(f"Gates: PF >= {GATE_PF_MIN}, trades >= {GATE_TRADES_MIN}, "
            f"max DD ({dd_summary})\n")

    passed: list[tuple] = []
    failed: list[tuple[str, str]] = []

    for name, pair_spec in candidates.items():
        try:
            report = replay_pair(
                bars=args.bars,
                asset_name=name,
                long_symbol=pair_spec["long_symbol"],
                short_symbol=pair_spec["short_symbol"],
                interval=pair_spec["interval"],
                cfg=pair_spec["cfg"],
            )
        except Exception as e:  # noqa: BLE001
            print(f"  {name:10s}  ERROR: {e}")
            failed.append((name, f"replay error: {e}"))
            continue

        pf      = report.profit_factor
        n       = report.n_trades
        dd      = report.max_drawdown_pct
        wr      = report.win_rate
        total   = report.total_return_pct
        years   = _interval_to_years(pair_spec["interval"], args.bars)
        dd_gate = _dd_gate(pair_spec["interval"])
        verdict = _format_verdict(pf, n, dd, dd_gate)
        print(f"  {name:10s}  PF={pf:6.2f}  n={n:3d}  WR={wr:5.1f}%  "
                f"total={total:+6.1f}%  maxDD={dd:5.1f}%  "
                f"window={years:.2f}yr  → {verdict}")

        if verdict == "PASS":
            passed.append((name, pf, n, dd, wr, total, years,
                             pair_spec["long_symbol"],
                             pair_spec["short_symbol"]))
        else:
            failed.append((name, verdict))

    print(f"\n=== SUMMARY ===")
    print(f"  PASS:  {len(passed)} / {len(candidates)}")
    for name, pf, n, dd, wr, total, years, ls, ss in passed:
        print(f"    promote: {name}  ({ls}/{ss})  "
                f"PF={pf:.2f}  n={n}  WR={wr:.1f}%  "
                f"total={total:+.1f}%  DD={dd:.1f}%  window={years:.2f}yr")
    if failed:
        print(f"  fail:  {len(failed)}")
        for name, why in failed:
            print(f"    skip:    {name}  ({why})")
    if passed:
        print(f"\nPromoting a pair candidate requires more work than promoting")
        print(f"a momentum/breakout asset: pair_main.py currently runs a single")
        print(f"hard-coded ETH/BTC pair. Refactoring to support multiple pairs")
        print(f"is a follow-up step — for now these results indicate which")
        print(f"pairs are worth that effort.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
