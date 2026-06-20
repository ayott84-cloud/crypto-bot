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

def _fetch_klines(symbol: str, interval: str, count: int,
                    source: str = "weex") -> pd.DataFrame:
    """Pull klines for replay.

    source:
      "weex"    — live exchange (default). Hardcapped at 1000 bars per
                  call; if count > 1000 you'll only get the most recent
                  1000 anyway.
      "binance" — Binance USDT-M futures public klines, chained backward
                  in 1500-bar chunks. Use for extended-window backtests
                  on strategies whose signal is too sparse for the WEEX
                  cap (e.g. 5m scalp needing ~5000 bars for n>=20).
                  Top-10 perp prices are arbitraged tight enough that
                  Binance klines are a clean proxy for WEEX backtests.

    Both sources return positional kline rows (the first 6 columns —
    open_time, open, high, low, close, volume — match), so
    signals.build_dataframe handles either layout identically.
    """
    from signals import build_dataframe
    if source == "binance":
        from tools._binance_klines import fetch_klines_chained
        raw = fetch_klines_chained(symbol, interval, count)
    else:
        from executor import Executor
        ex = Executor()
        raw = ex.get_klines(symbol, interval, min(count, 1000))
    if not raw:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = build_dataframe(raw)
    # Replay loops index by integer position, not by timestamp
    return df.reset_index(drop=False).dropna(subset=["close"]).reset_index(drop=True)


# ─── Momentum replay ───────────────────────────────────────────────────────

def replay_momentum(asset_name: str, cfg: dict, bars: int = 500) -> BacktestReport:
    """Replay momentum entries+exits over the last `bars` of klines.

    Uses the SAME analyze_entry_signal / check_exit_conditions the live
    bot uses, so the report reflects exactly what the bot would have
    done — minus rotation logic (single-position-per-asset replay).

    Two-phase exits: TP1 partially closes, then breakeven SL / TP2 / stale
    decide the rest. We treat TP1 as a separate trade and the remainder
    as another trade so PnL accounting matches the journal's row count.
    """
    from signals import (
        compute_indicators, analyze_entry_signal, check_exit_conditions,
    )

    df = _fetch_klines(cfg["symbol"], cfg["interval"], bars)
    if df is None or len(df) == 0:
        return BacktestReport(bot="momentum", asset=asset_name, bars_seen=0)

    df = compute_indicators(df, cfg)

    # BTC context for correlation filter (cfg.use_btc_filter)
    btc_close_series = None
    btc_ema_series   = None
    if cfg.get("use_btc_filter"):
        try:
            btc_df = _fetch_klines("BTCUSDT", cfg["interval"], bars)
            ema_period = cfg.get("btc_ema_period", 50)
            btc_df["btc_ema"] = btc_df["close"].ewm(span=ema_period, adjust=False).mean()
            btc_close_series = btc_df["close"]
            btc_ema_series   = btc_df["btc_ema"]
        except Exception:  # noqa: BLE001
            pass

    report = BacktestReport(bot="momentum", asset=asset_name, bars_seen=len(df))

    start = max(
        cfg.get("ema_slow", 50),
        cfg.get("atr_sma_period", 20),
        cfg.get("rsi_period", 14),
        cfg.get("macd_slow", 26),
    ) + 5
    position: dict | None = None

    for i in range(start, len(df)):
        window = df.iloc[: i + 1]

        if position is None:
            btc_c = btc_e = None
            if btc_close_series is not None and i < len(btc_close_series):
                btc_c = float(btc_close_series.iloc[i]) if not pd.isna(btc_close_series.iloc[i]) else None
                btc_e = float(btc_ema_series.iloc[i])   if not pd.isna(btc_ema_series.iloc[i])   else None
            sig = analyze_entry_signal(window, cfg, btc_close=btc_c, btc_ema=btc_e)
            if sig["would_enter"]:
                position = {
                    "entry_bar":    i,
                    "entry_price":  float(df.iloc[i]["close"]),
                    "atr_at_entry": float(df.iloc[i]["atr"]),
                    "phase":        "full",
                }
            continue

        bars_since_entry = i - position["entry_bar"]
        current_price    = float(df.iloc[i]["close"])
        reason, kind = check_exit_conditions(
            entry_price=position["entry_price"],
            atr_at_entry=position["atr_at_entry"],
            current_price=current_price,
            bars_since_entry=bars_since_entry,
            phase=position["phase"],
            cfg=cfg,
        )
        if reason is None:
            continue

        sign = 1.0  # momentum is LONG only by default for candidate validation
        pnl_pct = sign * (current_price - position["entry_price"]) / position["entry_price"] * 100

        if kind == "partial":
            # TP1 — close half (model as 50% of the move) and continue
            report.trades.append(TradeResult(
                direction="LONG", entry_bar=position["entry_bar"],
                exit_bar=i, entry_price=position["entry_price"],
                exit_price=current_price, exit_reason=reason,
                pnl_pct=pnl_pct * 0.5,  # half the position
            ))
            position["phase"] = "tp1_taken"
        else:
            # Full exit (SL / TP2 / stale / BE)
            remaining_factor = 0.5 if position["phase"] == "tp1_taken" else 1.0
            report.trades.append(TradeResult(
                direction="LONG", entry_bar=position["entry_bar"],
                exit_bar=i, entry_price=position["entry_price"],
                exit_price=current_price, exit_reason=reason,
                pnl_pct=pnl_pct * remaining_factor,
            ))
            position = None
    return report


# ─── Scalp replay ─────────────────────────────────────────────────────────

def replay_scalp(asset_name: str, cfg: dict, bars: int = 1000,
                   source: str = "weex") -> BacktestReport:
    """Phase M — replay the 5m vol-expansion + new-high scalp strategy.

    Exit is pure-pct bracket (no ATR scaling). Once a position opens, the
    next bar's high/low (vs SL/TP thresholds) determines whether it
    closes; for simplicity we use the bar CLOSE as the trigger price
    (conservative: real fills can be better with limit orders or worse
    with market slippage). Cooldown logic from the live bot is mirrored
    so the replay matches live behavior.

    source: "weex" (default, hardcapped at 1000 bars) or "binance"
    (chained, any window). Use Binance for windows > 3.5 days on 5m.
    """
    from scalp_signals import analyze_scalp_entry, check_scalp_exit

    df = _fetch_klines(cfg["symbol"], cfg["interval"], bars, source=source)
    if df is None or len(df) == 0:
        return BacktestReport(bot="scalp", asset=asset_name, bars_seen=0)

    # Phase M.2: resample 5m → 1h for the higher-TF trend gate. Cheaper
    # than chaining another Coinbase fetch + guarantees time alignment.
    # On 5m data with ts index, "1h" rolling buckets aggregate cleanly.
    df_1h_full = None
    if cfg.get("use_higher_tf_trend", False) and cfg.get("interval") == "5m":
        try:
            df_1h_full = df["close"].resample("1h").last().to_frame()
            df_1h_full["close"] = df_1h_full["close"].ffill()
            ef = int(cfg.get("higher_tf_ema_fast", 20))
            es = int(cfg.get("higher_tf_ema_slow", 50))
            df_1h_full["ema_fast"] = df_1h_full["close"].ewm(span=ef, adjust=False).mean()
            df_1h_full["ema_slow"] = df_1h_full["close"].ewm(span=es, adjust=False).mean()
        except Exception:  # noqa: BLE001
            df_1h_full = None

    needed = max(cfg.get("range_long_sma", 50),
                  cfg.get("momentum_lookback", 20),
                  cfg.get("new_high_lookback", 20),
                  cfg.get("vol_sma_period", 20),
                  cfg.get("rsi_period", 14)) + 2
    report = BacktestReport(bot="scalp", asset=asset_name, bars_seen=len(df))

    position: dict | None = None
    cooldown_until_bar = -1  # index up to which re-entry is blocked

    for i in range(needed, len(df)):
        window = df.iloc[: i + 1]
        current_close = float(df.iloc[i]["close"])

        if position is None:
            if i < cooldown_until_bar:
                continue
            # Slice the 1h DF to "as of this bar's timestamp" so we
            # don't leak future trend info into past-bar entry decisions.
            df_1h_slice = None
            if df_1h_full is not None:
                cutoff_ts = window.index[-2]  # last completed bar
                df_1h_slice = df_1h_full.loc[df_1h_full.index <= cutoff_ts]
                if len(df_1h_slice) < 50:
                    df_1h_slice = None
            sig = analyze_scalp_entry(window, cfg, df_1h=df_1h_slice)
            if sig["would_enter"]:
                position = {
                    "direction":     sig["direction"],
                    "entry_bar":     i,
                    "entry_price":   current_close,
                }
            continue

        # In a position — check pct-bracket exit
        reason = check_scalp_exit(
            entry_price=position["entry_price"],
            current_price=current_close,
            direction=position["direction"],
            cfg=cfg,
        )
        if reason:
            sign = 1.0 if position["direction"] == "LONG" else -1.0
            pnl_pct = sign * (current_close - position["entry_price"]) \
                        / position["entry_price"] * 100
            report.trades.append(TradeResult(
                direction=position["direction"],
                entry_bar=position["entry_bar"], exit_bar=i,
                entry_price=position["entry_price"],
                exit_price=current_close,
                exit_reason=reason, pnl_pct=pnl_pct,
            ))
            # Apply cooldown: SCALP_COOLDOWN_SECONDS / 5min ≈ 2 bars
            cooldown_until_bar = i + 2
            position = None
    return report


# ─── Breakout replay ───────────────────────────────────────────────────────

def replay_breakout(asset_name: str, cfg: dict, bars: int = 500) -> BacktestReport:
    from breakout_signals import (
        compute_donchian_channels, analyze_breakout_entry, check_breakout_exit,
    )
    from breakout_main import _compute_indicators  # ATR/ATR_SMA/ADX

    df = _fetch_klines(cfg["symbol"], cfg["interval"], bars)
    df = _compute_indicators(df, cfg)

    # G.2: fetch 1D series for the trend gate
    df_1d_full = None
    if cfg.get("use_trend_filter", False):
        df_1d_full = _fetch_klines(cfg["symbol"], "1d", min(bars, 365))
        if df_1d_full is not None and len(df_1d_full) >= 50:
            df_1d_full["ema_fast"] = df_1d_full["close"].ewm(span=20, adjust=False).mean()
            df_1d_full["ema_slow"] = df_1d_full["close"].ewm(span=50, adjust=False).mean()

    report = BacktestReport(bot="breakout", asset=asset_name, bars_seen=len(df))

    start = cfg.get("donchian_period", 20) + cfg.get("atr_sma_period", 20) + 5
    position = None

    for i in range(start, len(df)):
        window = df.iloc[: i + 1]
        if position is None:
            # Use whatever 1D bars are available; live bot does the same
            sig = analyze_breakout_entry(window, cfg, df_1d=df_1d_full)
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
    rsi_full = compute_rsi_vwap(df, length=cfg.get("rsi_length", 15),
                                  source=cfg.get("rsi_source", "vwap"))
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

def replay_pair(bars: int = 500, asset_name: str | None = None,
                 long_symbol: str | None = None,
                 short_symbol: str | None = None,
                 interval: str | None = None,
                 cfg: dict | None = None) -> BacktestReport:
    """Replay pair entries+exits. Defaults to ETH/BTC from pair_config;
    every argument is overridable so the same function validates
    candidate pairs (BTC/SOL, ETH/SOL, etc.)."""
    from pair_signals import (
        compute_ratio, rolling_z_score, analyze_pair_entry, check_pair_exit,
    )
    from pair_config import (
        PAIR_CONFIG, PAIR_INTERVAL, PAIR_LONG_SYMBOL, PAIR_SHORT_SYMBOL,
    )

    long_symbol  = long_symbol  or PAIR_LONG_SYMBOL
    short_symbol = short_symbol or PAIR_SHORT_SYMBOL
    interval     = interval     or PAIR_INTERVAL
    cfg          = cfg          or PAIR_CONFIG
    asset_name   = asset_name   or "ETHBTC"

    eth = _fetch_klines(long_symbol,  interval, bars)
    btc = _fetch_klines(short_symbol, interval, bars)
    n = min(len(eth), len(btc))
    eth_close = eth["close"].iloc[:n].reset_index(drop=True)
    btc_close = btc["close"].iloc[:n].reset_index(drop=True)
    report = BacktestReport(bot="pair", asset=asset_name, bars_seen=n)

    start = cfg.get("z_window", 30) + 2
    position = None
    for i in range(start, n):
        e_win = eth_close.iloc[: i + 1]
        b_win = btc_close.iloc[: i + 1]
        if position is None:
            sig = analyze_pair_entry(e_win, b_win, cfg)
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
                cfg=cfg,
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

def _run_momentum(bars: int) -> List[BacktestReport]:
    from config import ASSETS
    return [replay_momentum(name, cfg, bars=bars)
            for name, cfg in ASSETS.items()]


def _run_scalp(bars: int) -> List[BacktestReport]:
    from scalp_config import SCALP_ASSETS
    return [replay_scalp(name, cfg, bars=bars)
            for name, cfg in SCALP_ASSETS.items()]


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
    parser.add_argument("--bot",
                        choices=["momentum", "breakout", "pair", "reversal",
                                  "scalp", "all"],
                        default="all", help="Which strategy to replay")
    parser.add_argument("--bars", type=int, default=500,
                        help="How many historical bars to fetch per asset")
    args = parser.parse_args()

    runners = {
        "momentum": _run_momentum,
        "breakout": _run_breakout,
        "pair":     _run_pair,
        "reversal": _run_reversal,
        "scalp":    _run_scalp,
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
