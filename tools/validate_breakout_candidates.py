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
# Phase L gate tightening (Jun 19 2026): raised from 5 → 15 after
# Phase K round 3 promotions went 0-for-5 on first live entries.
# The n=5 threshold was too permissive — PF means little at that
# sample size. n>=15 still admits short windows but produces less
# small-sample blowback.
GATE_TRADES_MIN = 15

# TF-scaled DD gates. Drawdown is bar-frequency-dependent in trend
# strategies: a 1H signal sees more whipsaw stops (small DD), a daily
# signal holds through wider swings (larger DD), a weekly signal even
# more. Single-number gate was sloppy — round 4 showed perfectly-good
# 1D candidates failing on DD that would be normal for the timeframe.
GATE_DD_MAX_BY_TF = {
    "1h":  12.0,
    "4h":  18.0,
    "1d":  22.0,
    "1w":  28.0,
}
GATE_DD_DEFAULT = 18.0


_INTERVAL_HOURS = {
    "1m": 1 / 60,  "5m": 5 / 60,  "15m": 0.25,  "30m": 0.5,
    "1h": 1,       "2h": 2,       "4h": 4,      "6h": 6,
    "8h": 8,       "12h": 12,
    "1d": 24,      "1w": 168,
}


def _interval_to_years(interval: str, bars: int) -> float:
    """Convert (interval, bars) → window length in years.

    Returns 0 when interval is unknown so the validator output doesn't
    misrepresent the window with a guess.
    """
    hours = _INTERVAL_HOURS.get(interval.lower())
    if hours is None:
        return 0.0
    return (bars * hours) / (365.25 * 24)


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
                          help="Historical bars per asset (default 1000 — "
                                "WEEX caps kline limit at 1000, so requests "
                                "above that fail with -1142)")
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
    dd_summary = ", ".join(f"{tf}<={v:.0f}%"
                              for tf, v in GATE_DD_MAX_BY_TF.items())
    print(f"Gates: PF >= {GATE_PF_MIN}, trades >= {GATE_TRADES_MIN}, "
            f"max DD ({dd_summary})\n")

    passed: list[tuple[str, float, int, float]] = []
    failed: list[tuple[str, str]] = []

    for name, cfg in candidates.items():
        try:
            report = replay_breakout(asset_name=name, cfg=cfg, bars=args.bars)
        except Exception as e:  # noqa: BLE001
            print(f"  {name:10s}  ERROR: {e}")
            failed.append((name, f"replay error: {e}"))
            continue

        pf      = report.profit_factor
        n       = report.n_trades
        dd      = report.max_drawdown_pct
        wr      = report.win_rate
        total   = report.total_return_pct
        bars    = report.bars_seen
        cfg_iv  = cfg.get("interval", "")
        # Use ACTUAL bars seen (handles short Coinbase fetches correctly)
        years   = _interval_to_years(cfg_iv, bars)
        dd_gate = _dd_gate(cfg_iv)
        verdict = _format_verdict(pf, n, dd, dd_gate)
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
        print(f"\nCopy-paste BREAKOUT_BACKTEST_STATS entries:")
        for name, pf, n, dd, wr, total, years in passed:
            print(f'    "{name}": {{"pf": {pf:.2f}, "trades": {n}, '
                    f'"pnl_pct": {total:.1f}, "dd_pct": {dd:.1f}, '
                    f'"wr": {wr:.1f}, "years": {years:.2f}, '
                    f'"source": "1000-bar replay"}},')
    return 0


if __name__ == "__main__":
    sys.exit(main())
