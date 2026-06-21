"""Phase N.2 — crossover variant sweep.

Phase N retired with no edge (218 trades, mean WR ~31% vs 36% fee-adjusted
breakeven). This harness tests 15 systematic variants of the baseline
(timeframe × SMA pair × higher-TF trend filter × R/R bracket) to find
out whether any combination produces a config that clears the gate.

Variant matrix (15 picks balancing coverage vs runtime):
  A0 baseline       5m  50/100  no-filter  1%/2%    ← Phase N.X (no edge)
  A1 1h timeframe   1h  50/100  no-filter  1%/2%
  A2 15m timeframe  15m 50/100  no-filter  1%/2%
  A3 4h timeframe   4h  50/100  no-filter  1%/2%
  B1 5m + filter    5m  50/100  1h-trend   1%/2%
  B2 1h + filter    1h  50/100  1h-trend   1%/2%
  C1 5m 20/50       5m  20/50   no-filter  1%/2%
  C2 5m 9/21        5m  9/21    no-filter  1%/2%
  C3 5m 50/200      5m  50/200  no-filter  1%/2%
  C4 1h 20/50       1h  20/50   no-filter  1%/2%
  C5 1h 50/200      1h  50/200  no-filter  1%/2%
  D1 5m wider RR    5m  50/100  no-filter  1%/3%
  D2 1h wider RR    1h  50/100  no-filter  1%/3%
  D3 1h 1.5%/3%     1h  50/100  no-filter  1.5%/3%
  E1 combo          1h  20/50   1h-trend   1%/2%

Asset universe: BTC, ETH, SOL, XRP, DOGE, LINK (the 6 with full
Coinbase data depth in Phase N's validator run). ADA + AVAX skipped
(Coinbase returns <300 bars).

Cache strategy: each (symbol, timeframe) is fetched ONCE up front
(~24 API calls, ~3-5 min on Coinbase) and reused across all variant
runs. Total replay compute after fetches: ~30 sec for 90 strategy
evaluations.

Output: ranked leaderboard sorted by mean PF across the 6 assets,
plus per-variant stats. Phase N.2 winning criteria:
  - Mean PF >= 1.3 across at least 4 of 6 assets
  - Aggregate total return positive
  - No asset with DD > 25% (catastrophe filter)

Usage:
    venv/bin/python tools/sweep_crossover_variants.py
    venv/bin/python tools/sweep_crossover_variants.py --bars 5000
    venv/bin/python tools/sweep_crossover_variants.py --variant A1   # just one
"""

from __future__ import annotations

import argparse
import logging
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))


# ─── Sweep gate thresholds (Phase N.2 promotion criteria) ────────────
SWEEP_GATE_PF_MIN          = 1.3
SWEEP_GATE_ASSET_PASS_MIN  = 4    # of 6 working assets
SWEEP_GATE_DD_MAX          = 25.0  # any single asset DD above → fail


# ─── Variant matrix ──────────────────────────────────────────────────

@dataclass(frozen=True)
class Variant:
    code: str
    label: str
    interval: str
    sma_fast: int
    sma_slow: int
    use_filter: bool
    sl_pct: float
    tp_pct: float


VARIANTS = [
    # A — timeframe sweep (baseline strategy on different TFs)
    Variant("A0", "baseline           ", "5m",  50, 100, False, 1.0, 2.0),
    Variant("A1", "1h timeframe       ", "1h",  50, 100, False, 1.0, 2.0),
    Variant("A2", "15m timeframe      ", "15m", 50, 100, False, 1.0, 2.0),
    Variant("A3", "4h timeframe       ", "4h",  50, 100, False, 1.0, 2.0),
    # B — single filter addition (1h trend gate)
    Variant("B1", "5m + 1h-trend gate ", "5m",  50, 100, True,  1.0, 2.0),
    Variant("B2", "1h + 1h-trend gate ", "1h",  50, 100, True,  1.0, 2.0),
    # C — SMA-pair sweep at 5m and 1h
    Variant("C1", "5m 20/50           ", "5m",  20,  50, False, 1.0, 2.0),
    Variant("C2", "5m 9/21            ", "5m",   9,  21, False, 1.0, 2.0),
    Variant("C3", "5m 50/200          ", "5m",  50, 200, False, 1.0, 2.0),
    Variant("C4", "1h 20/50           ", "1h",  20,  50, False, 1.0, 2.0),
    Variant("C5", "1h 50/200          ", "1h",  50, 200, False, 1.0, 2.0),
    # D — wider R/R brackets (fee-drag mitigation)
    Variant("D1", "5m wider RR 1%/3%  ", "5m",  50, 100, False, 1.0, 3.0),
    Variant("D2", "1h wider RR 1%/3%  ", "1h",  50, 100, False, 1.0, 3.0),
    Variant("D3", "1h 1.5%/3% RR      ", "1h",  50, 100, False, 1.5, 3.0),
    # E — combo of best signals (1h + faster SMA + filter)
    Variant("E1", "combo 1h+20/50+gate", "1h",  20,  50, True,  1.0, 2.0),
]


# ─── Asset universe ──────────────────────────────────────────────────
# Phase N validator confirmed these 6 have full 5000-bar Coinbase depth
# on 5m. ADA + AVAX cut off at ~290 bars and are skipped.

SWEEP_ASSETS = [
    ("BTC",  "BTCUSDT"),
    ("ETH",  "ETHUSDT"),
    ("SOL",  "SOLUSDT"),
    ("XRP",  "XRPUSDT"),
    ("DOGE", "DOGEUSDT"),
    ("LINK", "LINKUSDT"),
]


# ─── Sweep execution ─────────────────────────────────────────────────

def _build_cfg(symbol: str, variant: Variant, debug_filter: bool = False) -> dict:
    return {
        "symbol":              symbol,
        "interval":            variant.interval,
        "sma_fast":            variant.sma_fast,
        "sma_slow":            variant.sma_slow,
        "sl_pct":              variant.sl_pct,
        "tp_pct":              variant.tp_pct,
        "allow_short":         True,
        "use_higher_tf_trend": variant.use_filter,
        "higher_tf_ema_fast":  20,
        "higher_tf_ema_slow":  50,
        "use_regime_gate":     False,
        "use_btc_filter":      False,
        "strategy_name":       f"{symbol} {variant.interval} Crossover",
        "_debug_filter":       debug_filter,
    }


def _required_timeframes(variants: list[Variant]) -> set[str]:
    return {v.interval for v in variants}


def _prefetch_klines(variants: list[Variant], bars: int) -> dict:
    """One API call per (symbol, timeframe) combination. Returns
    {(symbol, interval): DataFrame}. Bad fetches map to None and the
    sweep will skip them."""
    from tools.backtest_replay import _fetch_klines
    cache = {}
    pairs = [(asset, sym, tf)
              for asset, sym in SWEEP_ASSETS
              for tf in _required_timeframes(variants)]
    print(f"\n[Phase N.2 sweep] Pre-fetching {len(pairs)} (asset, timeframe) "
            f"combinations @ {bars} bars each...")
    t0 = time.time()
    for asset, symbol, tf in pairs:
        t1 = time.time()
        try:
            df = _fetch_klines(symbol, tf, bars, source="binance")
            n = len(df) if df is not None else 0
            cache[(symbol, tf)] = df
            tag = "OK   " if n >= bars * 0.9 else f"SHORT({n})"
            print(f"  {asset:4s} {tf:4s}: bars={n:5d}  [{tag}]  "
                    f"({time.time()-t1:.1f}s)")
        except Exception as e:  # noqa: BLE001
            cache[(symbol, tf)] = None
            print(f"  {asset:4s} {tf:4s}: FETCH ERROR — {e}")
    print(f"[fetch phase done — {time.time()-t0:.1f}s total]\n")
    return cache


def _run_variant(variant: Variant, cache: dict, debug_filter: bool = False) -> dict:
    """Run one variant across the 6 assets. Returns aggregate stats dict."""
    from tools.backtest_replay import replay_crossover
    results = []
    for asset, symbol in SWEEP_ASSETS:
        df = cache.get((symbol, variant.interval))
        if df is None or len(df) < variant.sma_slow + 50:
            results.append({"asset": asset, "skipped": True})
            continue
        cfg = _build_cfg(symbol, variant, debug_filter=debug_filter)
        try:
            report = replay_crossover(
                asset_name=asset, cfg=cfg, bars=len(df),
                source="binance", pre_fetched_df=df)
            results.append({
                "asset":    asset,
                "skipped":  False,
                "pf":       report.profit_factor,
                "n":        report.n_trades,
                "wr":       report.win_rate,
                "total":    report.total_return_pct,
                "dd":       report.max_drawdown_pct,
            })
        except Exception as e:  # noqa: BLE001
            results.append({"asset": asset, "skipped": True, "error": str(e)})
    return _aggregate(variant, results)


def _aggregate(variant: Variant, results: list[dict]) -> dict:
    eligible = [r for r in results if not r.get("skipped") and r["n"] >= 4]
    if not eligible:
        return {
            "variant": variant, "results": results,
            "mean_pf": 0.0, "median_pf": 0.0, "total_trades": 0,
            "total_return": 0.0, "max_dd": 0.0,
            "assets_passing_pf": 0, "assets_eligible": 0,
            "gate_pass": False,
        }
    pfs = [r["pf"] for r in eligible]
    total_trades = sum(r["n"] for r in eligible)
    # Average return per trade across eligible assets, weighted by trade count
    total_return_sum = sum(r["total"] for r in eligible)
    max_dd = max(r["dd"] for r in eligible)
    passing = sum(1 for r in eligible if r["pf"] >= SWEEP_GATE_PF_MIN)
    gate_pass = (
        passing >= SWEEP_GATE_ASSET_PASS_MIN
        and max_dd <= SWEEP_GATE_DD_MAX
        and total_return_sum > 0
    )
    return {
        "variant": variant, "results": results,
        "mean_pf":           statistics.mean(pfs),
        "median_pf":         statistics.median(pfs),
        "total_trades":      total_trades,
        "total_return":      total_return_sum,
        "max_dd":            max_dd,
        "assets_passing_pf": passing,
        "assets_eligible":   len(eligible),
        "gate_pass":         gate_pass,
    }


def _format_pf(pf: float) -> str:
    if pf >= 10:
        return ">10  "
    return f"{pf:5.2f}"


def _print_variant_detail(stats: dict) -> None:
    v = stats["variant"]
    flag = "PASS" if stats["gate_pass"] else "    "
    print(f"\n[{v.code}] {v.label}  {flag}")
    print(f"     meanPF={_format_pf(stats['mean_pf'])}  "
            f"medianPF={_format_pf(stats['median_pf'])}  "
            f"n={stats['total_trades']:4d}  "
            f"totRet={stats['total_return']:+7.1f}%  "
            f"maxDD={stats['max_dd']:5.1f}%  "
            f"pass={stats['assets_passing_pf']}/{stats['assets_eligible']}")
    for r in stats["results"]:
        if r.get("skipped"):
            why = r.get("error", "data-short or n<4")
            print(f"       {r['asset']:4s}: SKIP ({why})")
        else:
            print(f"       {r['asset']:4s}: "
                    f"PF={_format_pf(r['pf'])}  n={r['n']:3d}  "
                    f"WR={r['wr']:5.1f}%  tot={r['total']:+6.1f}%  "
                    f"DD={r['dd']:5.1f}%")


def _print_leaderboard(all_stats: list[dict]) -> None:
    print("\n" + "=" * 78)
    print("=== LEADERBOARD (sorted by mean PF across eligible assets) ===")
    print("=" * 78)
    print(f"{'rank':4s}  {'code':4s}  {'label':20s}  "
            f"{'meanPF':>6s}  {'medianPF':>8s}  "
            f"{'trades':>6s}  {'totRet':>7s}  "
            f"{'maxDD':>6s}  {'pass':>5s}  gate")
    print("-" * 78)
    sorted_stats = sorted(all_stats, key=lambda s: s["mean_pf"], reverse=True)
    for rank, s in enumerate(sorted_stats, 1):
        v = s["variant"]
        gate = "PASS" if s["gate_pass"] else "fail"
        print(f"{rank:4d}  {v.code:4s}  {v.label:20s}  "
                f"{_format_pf(s['mean_pf']):>6s}  "
                f"{_format_pf(s['median_pf']):>8s}  "
                f"{s['total_trades']:6d}  "
                f"{s['total_return']:+6.1f}%  "
                f"{s['max_dd']:5.1f}%  "
                f"{s['assets_passing_pf']:1d}/{s['assets_eligible']:1d}    "
                f"{gate}")


def main() -> int:
    logging.basicConfig(level=logging.WARNING,
                         format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--bars", type=int, default=5000,
                         help="Bars per (asset, timeframe). Default 5000. "
                              "Same value across all TFs gives roughly "
                              "comparable per-variant trade counts.")
    parser.add_argument("--variant", default=None,
                         help="Run only one variant by code (e.g. A1)")
    parser.add_argument("--debug-filter", action="store_true",
                         help="Print per-asset filter diagnostics for variants "
                              "with use_higher_tf_trend=True. Use to debug the "
                              "B1==A0 anomaly from the Jun 20 sweep.")
    args = parser.parse_args()

    variants = VARIANTS
    if args.variant:
        variants = [v for v in VARIANTS if v.code == args.variant.upper()]
        if not variants:
            print(f"  ERROR: variant code {args.variant} not in matrix")
            print(f"  available: {[v.code for v in VARIANTS]}")
            return 2

    print("=" * 78)
    print("Phase N.2 — crossover variant sweep")
    print(f"  {len(variants)} variants × {len(SWEEP_ASSETS)} assets = "
            f"{len(variants) * len(SWEEP_ASSETS)} replays")
    print(f"  Gate: mean PF >= {SWEEP_GATE_PF_MIN}, "
            f">= {SWEEP_GATE_ASSET_PASS_MIN}/{len(SWEEP_ASSETS)} assets passing PF, "
            f"max DD <= {SWEEP_GATE_DD_MAX:.0f}%, "
            f"aggregate return > 0")
    print("=" * 78)

    cache = _prefetch_klines(variants, args.bars)

    all_stats = []
    for variant in variants:
        stats = _run_variant(variant, cache, debug_filter=args.debug_filter)
        _print_variant_detail(stats)
        all_stats.append(stats)

    _print_leaderboard(all_stats)

    print("\n=== SUMMARY ===")
    winners = [s for s in all_stats if s["gate_pass"]]
    print(f"  PASS gate: {len(winners)} / {len(all_stats)}")
    if winners:
        print("\n  Variants worth implementing as live config:")
        for w in sorted(winners, key=lambda s: s["mean_pf"], reverse=True):
            v = w["variant"]
            print(f"    {v.code}  {v.label.strip()}  "
                    f"meanPF={w['mean_pf']:.2f}  "
                    f"totRet={w['total_return']:+.1f}%  "
                    f"DD={w['max_dd']:.1f}%")
    else:
        print("  No variant cleared the Phase N.2 gate.")
        print("  Recommendation: retire crossover primitive permanently.")
        print("  Best candidate (highest meanPF) listed first in leaderboard above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
