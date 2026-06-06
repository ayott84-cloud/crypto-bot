"""Backtest replay harness — Phase 2C.2.

Walks each strategy's signal logic bar-by-bar against historical klines
fetched from WEEX. Reports per-asset PF / WR / max-DD / trade count so
the operator can decide which assets pass the activation gate (e.g.
PF≥1.5 for breakout/pair, PF≥1.3 for reversal).

Usage:
    python tools/backtest_replay.py --bot breakout
    python tools/backtest_replay.py --bot pair
    python tools/backtest_replay.py --bot reversal
    python tools/backtest_replay.py --bot all

This is NOT a TradingView-equivalent backtest. No slippage, no funding
costs, no margin compounding. Use the numbers as a directional sanity
check — the activation decision still belongs to the operator after
reviewing.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))

import pandas as pd

logger = logging.getLogger("backtest_replay")


# ─── Result types ──────────────────────────────────────────────────────────

@dataclass
class TradeResult:
    direction:    str     # "LONG" or "SHORT"
    entry_bar:    int
    exit_bar:     int
    entry_price:  float
    exit_price:   float
    exit_reason:  str
    pnl_pct:      float   # gross % move, sign-aware

    @property
    def is_win(self) -> bool:
        return self.pnl_pct > 0


@dataclass
class BacktestReport:
    bot:         str
    asset:       str
    bars_seen:   int
    trades:      List[TradeResult] = field(default_factory=list)

    @property
    def n_trades(self) -> int: return len(self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades: return 0.0
        return sum(t.is_win for t in self.trades) / len(self.trades) * 100

    @property
    def gross_profit(self) -> float:
        return sum(t.pnl_pct for t in self.trades if t.pnl_pct > 0)

    @property
    def gross_loss(self) -> float:
        return abs(sum(t.pnl_pct for t in self.trades if t.pnl_pct < 0))

    @property
    def profit_factor(self) -> float:
        if self.gross_loss == 0:
            return float("inf") if self.gross_profit > 0 else 0.0
        return self.gross_profit / self.gross_loss

    @property
    def expectancy_pct(self) -> float:
        if not self.trades: return 0.0
        return sum(t.pnl_pct for t in self.trades) / len(self.trades)

    @property
    def total_return_pct(self) -> float:
        return sum(t.pnl_pct for t in self.trades)

    @property
    def max_drawdown_pct(self) -> float:
        if not self.trades: return 0.0
        peak = 0.0
        equity = 0.0
        dd = 0.0
        for t in self.trades:
            equity += t.pnl_pct
            peak = max(peak, equity)
            dd = min(dd, equity - peak)
        return abs(dd)

    def summary_line(self) -> str:
        return (
            f"{self.bot:9s} {self.asset:10s}  "
            f"trades={self.n_trades:4d}  WR={self.win_rate:5.1f}%  "
            f"PF={self.profit_factor:5.2f}  "
            f"total={self.total_return_pct:+6.1f}%  "
            f"maxDD={self.max_drawdown_pct:5.1f}%  "
            f"E[trade]={self.expectancy_pct:+5.2f}%"
        )


# ─── Klines fetcher ────────────────────────────────────────────────────────

def _fetch_klines(symbol: str, interval: str, count: int) -> pd.DataFrame:
    """Pull klines via the real executor (works in DRY_RUN too — read-only call).

    WEEX returns positional arrays, not dicts — delegates to the existing
    signals.build_dataframe() helper which knows the WEEX layout.
    """
    from executor import Executor
    from signals import build_dataframe
    ex = Executor()
    raw = ex.get_klines(symbol, interval, count)
    if not raw:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = build_dataframe(raw)
    # Replay loops index by integer position, not by timestamp
    return df.reset_index(drop=False).dropna(subset=["close"]).reset_index(drop=True)


# ─── Breakout replay ───────────────────────────────────────────────────────

def replay_breakout(asset_name: str, cfg: dict, bars: int = 500) -> BacktestReport:
    from breakout_signals import (
        compute_donchian_channels, analyze_breakout_entry, check_breakout_exit,
    )
    from breakout_main import _compute_indicators  # ATR/ATR_SMA/ADX

    df = _fetch_klines(cfg["symbol"], cfg["interval"], bars)
    df = _compute_indicators(df, cfg)
    report = BacktestReport(bot="breakout", asset=asset_name, bars_seen=len(df))

    # Need at least donchian_period bars before any entry consideration
    start = cfg.get("donchian_period", 20) + cfg.get("atr_sma_period", 20) + 5
    position = None  # None or dict {direction, entry_bar, entry_price, atr_at_entry}

    for i in range(start, len(df)):
        window = df.iloc[: i + 1]
        if position is None:
            sig = analyze_breakout_entry(window, cfg)
            if sig["would_enter"]:
                position = {
                    "direction":    sig["direction"],
                    "entry_bar":    i,
                    "entry_price":  float(df.iloc[i]["close"]),
                    "atr_at_entry": float(df.iloc[i]["atr"]),
                }
        else:
            reason, kind = check_breakout_exit(
                window,
                position_direction=position["direction"],
                entry_price=position["entry_price"],
                atr_at_entry=position["atr_at_entry"],
                current_adx=float(df.iloc[i].get("adx", 0) or 0),
                cfg=cfg,
            )
            if reason:
                exit_price = float(df.iloc[i]["close"])
                sign = 1.0 if position["direction"] == "LONG" else -1.0
                pnl_pct = sign * (exit_price - position["entry_price"]) / position["entry_price"] * 100
                report.trades.append(TradeResult(
                    direction=position["direction"],
                    entry_bar=position["entry_bar"], exit_bar=i,
                    entry_price=position["entry_price"], exit_price=exit_price,
                    exit_reason=reason, pnl_pct=pnl_pct,
                ))
                position = None
    return report


# ─── Reversal replay ──────────────────────────────────────────────────────

def replay_reversal(asset_name: str, cfg: dict, bars: int = 500) -> BacktestReport:
    from reversal_signals import analyze_reversal_entry, compute_rsi_vwap
    from reversal_main import _compute_atr

    df = _fetch_klines(cfg["symbol"], cfg["interval"], bars)
    rsi_full = compute_rsi_vwap(df, length=cfg.get("rsi_length", 15))
    atr_full = _compute_atr(df, length=cfg.get("atr_length", 14))
    report = BacktestReport(bot="reversal", asset=asset_name, bars_seen=len(df))

    start = max(cfg.get("range_sma_length", 14), cfg.get("rsi_length", 15)) + 5
    position = None
    sl_mult = cfg.get("sl_atr_mult", 1.5)
    tp1_mult = cfg.get("tp1_atr_mult", 1.0)
    max_hold = cfg.get("max_hold_bars", 24)

    for i in range(start, len(df)):
        window = df.iloc[: i + 1]
        if position is None:
            sig = analyze_reversal_entry(window, cfg, rsi_vwap_series=rsi_full.iloc[:i + 1])
            if sig["would_enter"]:
                atr_at_entry = float(atr_full.iloc[i])
                if atr_at_entry > 0:
                    position = {
                        "direction":    sig["direction"],
                        "entry_bar":    i,
                        "entry_price":  float(df.iloc[i]["close"]),
                        "atr_at_entry": atr_at_entry,
                    }
        else:
            curr_price = float(df.iloc[i]["close"])
            bars_held = i - position["entry_bar"]
            ep = position["entry_price"]
            atr = position["atr_at_entry"]
            is_long = position["direction"] == "LONG"
            reason = None
            if is_long:
                if curr_price <= ep - sl_mult * atr: reason = "SL Hit"
                elif curr_price >= ep + tp1_mult * atr: reason = "TP1 Hit"
            else:
                if curr_price >= ep + sl_mult * atr: reason = "SL Hit"
                elif curr_price <= ep - tp1_mult * atr: reason = "TP1 Hit"
            if not reason and bars_held >= max_hold:
                reason = "Time Stop"
            if reason:
                sign = 1.0 if is_long else -1.0
                pnl_pct = sign * (curr_price - ep) / ep * 100
                report.trades.append(TradeResult(
                    direction=position["direction"],
                    entry_bar=position["entry_bar"], exit_bar=i,
                    entry_price=ep, exit_price=curr_price,
                    exit_reason=reason, pnl_pct=pnl_pct,
                ))
                position = None
    return report


# ─── Pair replay ──────────────────────────────────────────────────────────

def replay_pair(bars: int = 500) -> BacktestReport:
    from pair_signals import (
        compute_ratio, rolling_z_score, analyze_pair_entry, check_pair_exit,
    )
    from pair_config import (
        PAIR_CONFIG, PAIR_INTERVAL, PAIR_LONG_SYMBOL, PAIR_SHORT_SYMBOL,
    )

    eth = _fetch_klines(PAIR_LONG_SYMBOL,  PAIR_INTERVAL, bars)
    btc = _fetch_klines(PAIR_SHORT_SYMBOL, PAIR_INTERVAL, bars)
    n = min(len(eth), len(btc))
    eth_close = eth["close"].iloc[:n].reset_index(drop=True)
    btc_close = btc["close"].iloc[:n].reset_index(drop=True)
    report = BacktestReport(bot="pair", asset="ETHBTC", bars_seen=n)

    start = PAIR_CONFIG.get("z_window", 30) + 2
    position = None
    for i in range(start, n):
        e_win = eth_close.iloc[: i + 1]
        b_win = btc_close.iloc[: i + 1]
        if position is None:
            sig = analyze_pair_entry(e_win, b_win, PAIR_CONFIG)
            if sig["would_enter"]:
                position = {
                    "direction":    sig["direction"],
                    "entry_bar":    i,
                    "entry_ratio":  float(e_win.iloc[-1] / b_win.iloc[-1]),
                    "entry_eth":    float(e_win.iloc[-1]),
                    "entry_btc":    float(b_win.iloc[-1]),
                }
        else:
            bars_held = i - position["entry_bar"]
            reason, _ = check_pair_exit(
                e_win, b_win,
                position_direction=position["direction"],
                bars_held=bars_held, entry_ratio=position["entry_ratio"],
                cfg=PAIR_CONFIG,
            )
            if reason:
                exit_eth = float(e_win.iloc[-1])
                exit_btc = float(b_win.iloc[-1])
                # PnL on both legs (long cheap, short rich). Dollar-neutral
                # approximation: pct move on each leg, summed with signs.
                long_eth = position["direction"] == "LONG_ETH_SHORT_BTC"
                eth_pct = (exit_eth - position["entry_eth"]) / position["entry_eth"] * 100
                btc_pct = (exit_btc - position["entry_btc"]) / position["entry_btc"] * 100
                pnl_pct = (eth_pct - btc_pct) if long_eth else (btc_pct - eth_pct)
                report.trades.append(TradeResult(
                    direction=position["direction"],
                    entry_bar=position["entry_bar"], exit_bar=i,
                    entry_price=position["entry_ratio"],
                    exit_price=float(exit_eth / exit_btc),
                    exit_reason=reason, pnl_pct=pnl_pct,
                ))
                position = None
    return report


# ─── CLI ───────────────────────────────────────────────────────────────────

def _run_breakout(bars: int) -> List[BacktestReport]:
    from breakout_config import BREAKOUT_ASSETS
    return [replay_breakout(name, cfg, bars=bars)
            for name, cfg in BREAKOUT_ASSETS.items()]


def _run_reversal(bars: int) -> List[BacktestReport]:
    from reversal_config import REVERSAL_ASSETS
    return [replay_reversal(name, cfg, bars=bars)
            for name, cfg in REVERSAL_ASSETS.items()]


def _run_pair(bars: int) -> List[BacktestReport]:
    return [replay_pair(bars=bars)]


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--bot", choices=["breakout", "pair", "reversal", "all"],
                        default="all", help="Which strategy to replay")
    parser.add_argument("--bars", type=int, default=500,
                        help="How many historical bars to fetch per asset")
    args = parser.parse_args()

    runners = {
        "breakout": _run_breakout,
        "pair":     _run_pair,
        "reversal": _run_reversal,
    }
    selected = list(runners) if args.bot == "all" else [args.bot]

    all_reports: List[BacktestReport] = []
    for bot in selected:
        print(f"\n=== {bot.upper()} ({args.bars} bars/asset) ===")
        try:
            reports = runners[bot](args.bars)
        except Exception as e:
            print(f"  ERROR fetching/replaying {bot}: {e}")
            continue
        for r in reports:
            print("  " + r.summary_line())
            all_reports.append(r)

    print("\n=== ACTIVATION GATES ===")
    gates = {"breakout": 1.5, "pair": 1.5, "reversal": 1.3}
    for r in all_reports:
        gate = gates.get(r.bot, 1.0)
        verdict = "PASS" if r.profit_factor >= gate and r.n_trades >= 10 else "fail"
        print(f"  {r.bot:9s} {r.asset:10s}  PF={r.profit_factor:5.2f}  "
              f"trades={r.n_trades:3d}  gate=PF≥{gate}  → {verdict}")


if __name__ == "__main__":
    main()
