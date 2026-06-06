"""Reversal signal diagnostics — figure out WHY no trades fire.

Fetches kline data via the executor and reports per-filter pass rates
so we can tell whether the issue is:
  - Data: WEEX returns fewer bars than requested
  - Range filter: 2.5× SMA gate too tight
  - RSI: extremes never hit
  - Dot polarity: close never sits in extreme 30%
  - Conjunction: filters each fire alone but never together

Usage:
  python tools/reversal_diagnose.py --asset BTC_1D --bars 1000
  python tools/reversal_diagnose.py --asset ETH_1D
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))

import pandas as pd

from reversal_signals import (
    is_extreme_bar, reversal_dot_polarity, compute_rsi_vwap,
)
from reversal_config import REVERSAL_ASSETS


def diagnose(asset_name: str, bars_requested: int) -> None:
    cfg = REVERSAL_ASSETS.get(asset_name)
    if not cfg:
        print(f"Unknown asset: {asset_name}")
        print(f"Configured: {list(REVERSAL_ASSETS)}")
        return

    print(f"\n=== {asset_name} ({cfg['symbol']} {cfg['interval']}) ===")
    print(f"Requesting {bars_requested} bars...")

    from executor import Executor
    from signals import build_dataframe
    ex = Executor()
    raw = ex.get_klines(cfg["symbol"], cfg["interval"], bars_requested)
    if not raw:
        print("  ERROR: no klines returned")
        return

    df = build_dataframe(raw).reset_index(drop=False).dropna(subset=["close"]).reset_index(drop=True)
    actual_bars = len(df)
    print(f"  Actually got {actual_bars} bars")
    if actual_bars < bars_requested:
        print(f"  WEEX API capped at {actual_bars} (requested {bars_requested})")

    if actual_bars < 30:
        print("  Too few bars to evaluate; aborting")
        return

    # Compute indicators
    rsi = compute_rsi_vwap(df, length=cfg.get("rsi_length", 15))

    # Per-bar pass rates
    range_mult = cfg.get("range_mult", 2.5)
    range_sma_len = cfg.get("range_sma_length", 14)
    oversold = cfg.get("oversold", 15.0)
    overbought = cfg.get("overbought", 85.0)
    close_pct = cfg.get("close_position_pct", 0.30)
    window_bars = cfg.get("window_bars", 3)

    # Count individual filter hits
    n_extreme = 0
    n_oversold = 0
    n_overbought = 0
    n_rsi_rising_at_oversold = 0
    n_rsi_falling_at_overbought = 0
    n_bullish_dot = 0
    n_bearish_dot = 0
    extreme_ranges = []  # for stats

    # Sample of extreme bars to inspect
    sample_extreme_bars = []

    start = max(range_sma_len, cfg.get("rsi_length", 15)) + 2
    for i in range(start, actual_bars):
        window = df.iloc[: i + 1]
        # Extreme bar at current
        if is_extreme_bar(window, range_mult=range_mult,
                          range_sma_length=range_sma_len):
            n_extreme += 1
            bar = window.iloc[-1]
            rng = float(bar["high"]) - float(bar["low"])
            sma = (window["high"] - window["low"]).iloc[-range_sma_len - 1:-1].mean()
            multiple = rng / sma if sma > 0 else 0
            if len(sample_extreme_bars) < 5:
                sample_extreme_bars.append({
                    "i": i, "range": rng, "sma": sma, "x": multiple,
                    "close_pos": (float(bar["close"]) - float(bar["low"])) / rng if rng > 0 else None,
                    "rsi": float(rsi.iloc[i]) if not pd.isna(rsi.iloc[i]) else None,
                })
            extreme_ranges.append(multiple)

        # RSI extremes
        rsi_now = float(rsi.iloc[i]) if not pd.isna(rsi.iloc[i]) else 50
        rsi_prev = float(rsi.iloc[i - 1]) if not pd.isna(rsi.iloc[i - 1]) else 50
        if rsi_now < oversold:
            n_oversold += 1
            if rsi_now > rsi_prev:
                n_rsi_rising_at_oversold += 1
        if rsi_now > overbought:
            n_overbought += 1
            if rsi_now < rsi_prev:
                n_rsi_falling_at_overbought += 1

        # Dot polarity at current
        pol = reversal_dot_polarity(window, close_position_pct=close_pct)
        if pol == "bullish":
            n_bullish_dot += 1
        elif pol == "bearish":
            n_bearish_dot += 1

    eligible = actual_bars - start
    print(f"\nEligible bars (after warmup): {eligible}")
    print(f"\n--- Individual filter pass rates ---")
    print(f"  Extreme range (≥ {range_mult}× SMA-{range_sma_len}):  {n_extreme:4d}  ({n_extreme / eligible * 100:.1f}%)")
    if extreme_ranges:
        max_mult = max(extreme_ranges)
        med_mult = sorted(extreme_ranges)[len(extreme_ranges) // 2]
        print(f"    extremes' x-multiple: max={max_mult:.2f}, median={med_mult:.2f}")
    print(f"  RSI(VWAP) < {oversold}:                              {n_oversold:4d}  ({n_oversold / eligible * 100:.1f}%)")
    print(f"     ...of which rising (LONG candidates):           {n_rsi_rising_at_oversold:4d}")
    print(f"  RSI(VWAP) > {overbought}:                              {n_overbought:4d}  ({n_overbought / eligible * 100:.1f}%)")
    print(f"     ...of which falling (SHORT candidates):         {n_rsi_falling_at_overbought:4d}")
    print(f"  Bullish dot (close in bottom {close_pct*100:.0f}%):           {n_bullish_dot:4d}  ({n_bullish_dot / eligible * 100:.1f}%)")
    print(f"  Bearish dot (close in top {close_pct*100:.0f}%):              {n_bearish_dot:4d}  ({n_bearish_dot / eligible * 100:.1f}%)")

    if sample_extreme_bars:
        print(f"\n--- Sample of extreme-range bars ---")
        for s in sample_extreme_bars:
            cp = f"{s['close_pos']:.2f}" if s['close_pos'] is not None else "N/A"
            rsi_str = f"{s['rsi']:.1f}" if s['rsi'] is not None else "N/A"
            print(f"  bar {s['i']}: range={s['range']:.2f} sma={s['sma']:.2f} "
                  f"x={s['x']:.2f} close_pos={cp} rsi={rsi_str}")
    else:
        # If no extreme bars, what's the max range/SMA ratio we ever saw?
        bar_range = (df["high"] - df["low"]).astype(float)
        max_x = 0
        for i in range(range_sma_len, actual_bars):
            sma = bar_range.iloc[i - range_sma_len:i].mean()
            if sma > 0:
                x = float(bar_range.iloc[i]) / sma
                max_x = max(max_x, x)
        print(f"\n--- No bars cleared the extreme threshold ---")
        print(f"  Max range/SMA ratio EVER seen: {max_x:.2f}x (threshold: {range_mult}x)")
        print(f"  Suggestion: lower range_mult to ~{max_x * 0.85:.1f} or below")

    # Conjunction check: any bar where ALL THREE align (within window)?
    n_conjunction = 0
    for i in range(start, actual_bars):
        window = df.iloc[: i + 1]
        rsi_now = float(rsi.iloc[i]) if not pd.isna(rsi.iloc[i]) else 50
        rsi_prev = float(rsi.iloc[i - 1]) if not pd.isna(rsi.iloc[i - 1]) else 50
        # LONG conjunction?
        if rsi_now < oversold and rsi_now > rsi_prev:
            for offset in range(window_bars):
                if (is_extreme_bar(window, range_mult=range_mult,
                                    range_sma_length=range_sma_len, bar_offset=offset)
                    and reversal_dot_polarity(window, close_position_pct=close_pct,
                                              bar_offset=offset) == "bullish"):
                    n_conjunction += 1
                    break
        elif rsi_now > overbought and rsi_now < rsi_prev:
            for offset in range(window_bars):
                if (is_extreme_bar(window, range_mult=range_mult,
                                    range_sma_length=range_sma_len, bar_offset=offset)
                    and reversal_dot_polarity(window, close_position_pct=close_pct,
                                              bar_offset=offset) == "bearish"):
                    n_conjunction += 1
                    break

    print(f"\n--- Conjunction (all three filters align in {window_bars}-bar window) ---")
    print(f"  Total entry signals: {n_conjunction}")
    if n_conjunction == 0:
        print(f"  Diagnosis: {'no extreme bars' if n_extreme == 0 else 'extreme bars exist but RSI/dot never co-align'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--asset", default=None,
                         help="Asset name from REVERSAL_ASSETS (default: all)")
    parser.add_argument("--bars", type=int, default=1000)
    args = parser.parse_args()

    if args.asset:
        diagnose(args.asset, args.bars)
    else:
        for name in REVERSAL_ASSETS:
            diagnose(name, args.bars)


if __name__ == "__main__":
    main()
