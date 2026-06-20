"""Phase N — Crossover candidate validator.

Mirrors tools/validate_scalp_candidates.py. Runs replay_crossover on
each entry in CROSSOVER_ASSETS + CROSSOVER_CANDIDATE_ASSETS (so the
initial backtest sweep covers both promoted and queued candidates),
gates each result, prints PASS/fail.

Usage:
    cd /home/bot/crypto-bot
    venv/bin/python tools/validate_crossover_candidates.py
    venv/bin/python tools/validate_crossover_candidates.py --asset BTC_5M
    venv/bin/python tools/validate_crossover_candidates.py --bars 10000

Default: 5000 bars × extended-source = ~17 days of 5m history. Uses
Coinbase Exchange (helper retained the `binance` name for backward
compat) chained in 300-bar chunks.

Live trading routes through WEEX — this is backtest-only.

WEEX's kline API is hardcapped at 1000 bars per call with no
startTime/endTime support, so --source=weex caps at 1000 bars
regardless of --bars.
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
# Crossover is a SPARSER signal than scalp's M.2 filter stack —
# golden/death crosses fire only a few times per asset per week on
# 5m. n>=4 mirrors the scalp gate: consistent direction across many
# assets is the trust signal, not n per asset.
GATE_TRADES_MIN = 4

# TF-scaled DD — 5m gets the tightest gate. Crossover's tighter
# 1%/2% bracket means smaller per-trade swings than scalp's
# 1.5%/3%, so the 8% cap is generous.
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
                          help="Data source for klines. `binance` "
                                "actually uses Coinbase Exchange after "
                                "the Binance→Bybit→Coinbase migration "
                                "chain — label retained for backward "
                                "compat. `weex` is hardcapped at 1000 "
                                "bars.")
    parser.add_argument("--asset", default=None,
                          help="Validate just one candidate asset name "
                                "(e.g. BTC_5M)")
    parser.add_argument("--candidates-only", action="store_true",
                          help="Skip already-promoted CROSSOVER_ASSETS; "
                                "validate only CROSSOVER_CANDIDATE_ASSETS")
    args = parser.parse_args()

    try:
        from crossover_config import CROSSOVER_ASSETS, CROSSOVER_CANDIDATE_ASSETS
    except ImportError as e:
        print(f"  ERROR: cannot import crossover_config: {e}")
        return 2

    try:
        from tools.backtest_replay import replay_crossover
    except ImportError as e:
        print(f"  ERROR: cannot import replay_crossover: {e}")
        return 2

    candidates: dict = {}
    if not args.candidates_only:
        candidates.update(CROSSOVER_ASSETS)
    candidates.update(CROSSOVER_CANDIDATE_ASSETS)
    if args.asset:
        if args.asset not in candidates:
            print(f"  ERROR: {args.asset} not in CROSSOVER_ASSETS or CROSSOVER_CANDIDATE_ASSETS")
            print(f"  available: {', '.join(sorted(candidates.keys()))}")
            return 2
        candidates = {args.asset: candidates[args.asset]}

    print(f"\n=== CROSSOVER CANDIDATE VALIDATION ({args.bars} bars/asset) ===")
    dd_summary = ", ".join(f"{tf}<={v:.0f}%"
                              for tf, v in GATE_DD_MAX_BY_TF.items())
    print(f"Gates: PF >= {GATE_PF_MIN}, trades >= {GATE_TRADES_MIN}, "
            f"max DD ({dd_summary})\n")

    passed: list[tuple] = []
    failed: list[tuple[str, str]] = []

    for name, cfg in candidates.items():
        try:
            report = replay_crossover(asset_name=name, cfg=cfg,
                                         bars=args.bars, source=args.source)
        except Exception as e:  # noqa: BLE001
            print(f"  {name:10s}  ERROR: {e}")
            failed.append((name, f"replay error: {e}"))
            continue

        pf       = report.profit_factor
        n        = report.n_trades
        dd       = report.max_drawdown_pct
        wr       = report.win_rate
        total    = report.total_return_pct
        bars     = report.bars_seen
        cfg_iv   = cfg.get("interval", "")
        years    = _interval_to_years(cfg_iv, args.bars)
        dd_gate  = _dd_gate(cfg_iv)
        verdict  = _format_verdict(pf, n, dd, dd_gate)
        # Show bars_seen so data-truncation issues are visible — Phase N
        # exposed a case where the fetcher silently returned <<bars for
        # non-BTC assets, producing misleading n=1 verdicts.
        data_tag = ("DATA-OK" if bars >= int(args.bars * 0.9)
                     else f"DATA-SHORT({bars}/{args.bars})")
        print(f"  {name:10s}  bars={bars:5d}  PF={pf:6.2f}  n={n:3d}  WR={wr:5.1f}%  "
                f"total={total:+6.1f}%  maxDD={dd:5.1f}%  "
                f"window={years:.2f}yr  [{data_tag}]  → {verdict}")

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
        print(f"\nCopy-paste CROSSOVER_BACKTEST_STATS entries:")
        for name, pf, n, dd, wr, total, years in passed:
            print(f'    "{name}": {{"pf": {pf:.2f}, "trades": {n}, '
                    f'"pnl_pct": {total:.1f}, "dd_pct": {dd:.1f}, '
                    f'"wr": {wr:.1f}, "years": {years:.2f}, '
                    f'"source": "{args.bars}-bar replay"}},')
    return 0


if __name__ == "__main__":
    sys.exit(main())
