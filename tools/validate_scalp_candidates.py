"""Phase M — Scalp candidate validator.

Mirrors tools/validate_breakout_candidates.py. Runs replay_scalp on
each entry in SCALP_ASSETS + SCALP_CANDIDATE_ASSETS (so Phase M.6's
day-14 review uses one command that covers both promoted and queued
candidates), gates each result, prints PASS/fail.

Usage:
    cd /home/bot/crypto-bot
    venv/bin/python tools/validate_scalp_candidates.py
    venv/bin/python tools/validate_scalp_candidates.py --asset BTC_5M
    venv/bin/python tools/validate_scalp_candidates.py --bars 10000  # ~34 days

Default: 5000 bars × Binance source = ~17 days of 5m history. Binance
fetches in 1500-bar chunks chaining backward in time; top-10 perp prices
on Binance are arbitraged tight enough to WEEX that they're a clean
backtest proxy. Live trading still routes through WEEX — this is
backtest-only.

WEEX's kline API is hardcapped at 1000 bars per call with no
startTime/endTime support, so --source=weex caps at 1000 bars regardless
of --bars.
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
# Phase L gate tightening — also applied here for consistency
GATE_TRADES_MIN = 20

# TF-scaled DD — 5m gets the tightest gate since trades fire frequently
# and small per-trade swings accumulate fast.
GATE_DD_MAX_BY_TF = {
    "5m":  8.0,
    "1h":  12.0,
    "4h":  18.0,
    "1d":  22.0,
}
GATE_DD_DEFAULT = 12.0


_INTERVAL_HOURS = {
    "1m": 1 / 60, "5m": 5 / 60, "15m": 0.25, "30m": 0.5,
    "1h": 1, "2h": 2, "4h": 4, "6h": 6, "8h": 8, "12h": 12,
    "1d": 24, "1w": 168,
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
    parser.add_argument("--bars", type=int, default=5000,
                          help="Historical bars per asset (default 5000; "
                                "5m × 5000 = ~17 days. Requires "
                                "--source=binance for >1000 bars since "
                                "WEEX is capped.)")
    parser.add_argument("--source", choices=["weex", "binance"],
                          default="binance",
                          help="Data source for klines. binance allows "
                                "chained windows up to any size; weex "
                                "is hardcapped at 1000.")
    parser.add_argument("--asset", default=None,
                          help="Validate just one candidate asset name "
                                "(e.g. BTC_5M)")
    parser.add_argument("--candidates-only", action="store_true",
                          help="Skip already-promoted SCALP_ASSETS; validate "
                                "only SCALP_CANDIDATE_ASSETS")
    args = parser.parse_args()

    try:
        from scalp_config import SCALP_ASSETS, SCALP_CANDIDATE_ASSETS
    except ImportError as e:
        print(f"  ERROR: cannot import scalp_config: {e}")
        return 2

    try:
        from tools.backtest_replay import replay_scalp
    except ImportError as e:
        print(f"  ERROR: cannot import replay_scalp: {e}")
        return 2

    candidates: dict = {}
    if not args.candidates_only:
        candidates.update(SCALP_ASSETS)
    candidates.update(SCALP_CANDIDATE_ASSETS)
    if args.asset:
        if args.asset not in candidates:
            print(f"  ERROR: {args.asset} not in SCALP_ASSETS or SCALP_CANDIDATE_ASSETS")
            print(f"  available: {', '.join(sorted(candidates.keys()))}")
            return 2
        candidates = {args.asset: candidates[args.asset]}

    print(f"\n=== SCALP CANDIDATE VALIDATION ({args.bars} bars/asset) ===")
    dd_summary = ", ".join(f"{tf}<={v:.0f}%"
                              for tf, v in GATE_DD_MAX_BY_TF.items())
    print(f"Gates: PF >= {GATE_PF_MIN}, trades >= {GATE_TRADES_MIN}, "
            f"max DD ({dd_summary})\n")

    passed: list[tuple] = []
    failed: list[tuple[str, str]] = []

    for name, cfg in candidates.items():
        try:
            report = replay_scalp(asset_name=name, cfg=cfg,
                                     bars=args.bars, source=args.source)
        except Exception as e:  # noqa: BLE001
            print(f"  {name:10s}  ERROR: {e}")
            failed.append((name, f"replay error: {e}"))
            continue

        pf      = report.profit_factor
        n       = report.n_trades
        dd      = report.max_drawdown_pct
        wr      = report.win_rate
        total   = report.total_return_pct
        cfg_iv  = cfg.get("interval", "")
        years   = _interval_to_years(cfg_iv, args.bars)
        dd_gate = _dd_gate(cfg_iv)
        verdict = _format_verdict(pf, n, dd, dd_gate)
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
        print(f"\nCopy-paste SCALP_BACKTEST_STATS entries:")
        for name, pf, n, dd, wr, total, years in passed:
            print(f'    "{name}": {{"pf": {pf:.2f}, "trades": {n}, '
                    f'"pnl_pct": {total:.1f}, "dd_pct": {dd:.1f}, '
                    f'"wr": {wr:.1f}, "years": {years:.2f}, '
                    f'"source": "{args.bars}-bar replay"}},')
    return 0


if __name__ == "__main__":
    sys.exit(main())
