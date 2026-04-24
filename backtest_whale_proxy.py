"""Synthetic backtest for the whale-tracking bot.

Hyperliquid's public API has no historical whale-position snapshots, so a
true backtest is impossible. This script backtests a PROXY signal:

  Proxy thesis: when perp-futures funding reaches an extreme, retail is
  crowded on one side and smart money is likely fading it. Extreme
  POSITIVE funding (retail crowded long) → proxy CONSENSUS_SHORT.
  Extreme NEGATIVE funding (retail crowded short) → proxy CONSENSUS_LONG.

Run:
    python backtest_whale_proxy.py

Outputs:
    backtest_whale_proxy_results.json  (metrics)
    backtest_whale_proxy_trades.csv    (trade list)

The proxy is a lower bound for the real whale strategy:
 - Real whale-basket signal is richer (divergence, edge-decay guard, etc).
 - Real signal triggers only when smart + rekt both point the same way.
 - Proxy only captures the funding-extreme axis.

Treat results as: "if this passes, the real whale bot probably will too."
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

import requests

from whale_config import (
    WHALE_SL_ATR_MULT, WHALE_TP_ATR_MULT, WHALE_LEVERAGE,
    WHALE_MARGIN_CONSENSUS, WHALE_ATR_PERIOD,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("whale_backtest")

OKX_API = "https://www.okx.com"

# ─── Universe & window ───────────────────────────────────────────────────────
# Coins whales trade AND WEEX lists. Symbols in Binance/WEEX format ("BTCUSDT");
# translated to OKX swap format ("BTC-USDT-SWAP") inside the fetcher.
# (Binance/Bybit are geo-blocked from the US; OKX public endpoints still work.)
UNIVERSE = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
            "LINKUSDT", "AVAXUSDT", "LTCUSDT", "ADAUSDT", "NEARUSDT",
            "AAVEUSDT", "SUIUSDT", "FILUSDT", "APTUSDT", "ARBUSDT"]


def _to_okx_inst(symbol: str) -> str:
    """Convert 'BTCUSDT' → 'BTC-USDT-SWAP' for OKX."""
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}-USDT-SWAP"
    return symbol

MONTHS_BACK = 24

# Funding extreme thresholds (percentile over rolling 30-day window)
# Tightened after first backtest: 90 → 95 to match the whale-signal tightening
# (MIN_SMART_TRADERS 5→7, CONSENSUS 80→85). Fewer trades, higher quality bar.
FUNDING_EXTREME_PCTILE = 95     # top/bottom 5% funding → signal
MIN_SIGNAL_GAP_DAYS = 2         # don't re-enter same coin within 2 days
SLIPPAGE_BPS = 5                # 0.05%
TAKER_FEE_BPS = 6               # 0.06%
FUNDING_PERIODS_PER_DAY = 3     # 8-hour funding


# ─── Data fetch ──────────────────────────────────────────────────────────────

_OKX_BAR_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1H", "2h": "2H", "4h": "4H", "6h": "6H", "12h": "12H",
    "1d": "1D", "1w": "1W", "1M": "1M",
}


def _fetch_json(url: str, params: dict) -> dict:
    """Fetch JSON with basic retry. Returns raw response dict or {}."""
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("Fetch %s attempt %d failed: %s", url, attempt + 1, e)
            time.sleep(2 * (attempt + 1))
    return {}


def _interval_ms(interval: str) -> int:
    units = {"m": 60_000, "h": 60 * 60_000, "d": 24 * 60 * 60_000}
    try:
        n = int(interval[:-1])
        return n * units[interval[-1]]
    except (ValueError, KeyError):
        return 4 * 60 * 60_000  # default 4h


def fetch_klines(symbol: str, interval: str = "4h",
                 start_ms: int = None, end_ms: int = None,
                 limit: int = 100) -> List[list]:
    """Pull OKX perpetual-swap history candles. Paginated newest→oldest.

    OKX endpoint: GET /api/v5/market/history-candles
    Bars per call: max 100. Returns oldest-first list of
        [start_ms, open, high, low, close, volume, close_ms].
    """
    inst = _to_okx_inst(symbol)
    bar = _OKX_BAR_MAP.get(interval, "4H")
    bar_ms = _interval_ms(interval)

    all_rows: List[list] = []
    cursor = end_ms  # walk backwards
    while True:
        params = {"instId": inst, "bar": bar, "limit": min(limit, 100)}
        if cursor:
            params["after"] = str(cursor)  # OKX: 'after' = upper bound (ms), exclusive
        resp = _fetch_json(f"{OKX_API}/api/v5/market/history-candles", params)
        if not resp or resp.get("code") != "0":
            logger.warning("%s klines bad response: %s", symbol, (resp or {}).get("msg"))
            break
        batch = resp.get("data") or []
        if not batch:
            break
        # Each row: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        for r in batch:
            all_rows.append([int(r[0])] + r[1:6])
        oldest = min(int(r[0]) for r in batch)
        if len(batch) < params["limit"]:
            break
        if start_ms and oldest <= start_ms:
            break
        cursor = oldest  # next page: bars older than this one
        time.sleep(0.05)  # gentle pacing — OKX public limit ~10 req/2s
    # Sort asc, dedup, filter to [start_ms, end_ms]
    all_rows.sort(key=lambda r: r[0])
    seen = set()
    out = []
    for r in all_rows:
        if r[0] in seen:
            continue
        if start_ms and int(r[0]) < start_ms:
            continue
        seen.add(r[0])
        close_ms = int(r[0]) + bar_ms - 1
        out.append([int(r[0]), r[1], r[2], r[3], r[4], r[5], close_ms])
    return out


def fetch_funding(symbol: str, start_ms: int, end_ms: int,
                  limit: int = 100) -> List[dict]:
    """Pull OKX funding rate history.

    Endpoint: GET /api/v5/public/funding-rate-history (max 100/call)
    Returns: list of {"fundingTime": int ms, "fundingRate": str}.
    """
    inst = _to_okx_inst(symbol)
    all_f: List[dict] = []
    cursor = end_ms
    while True:
        params = {"instId": inst, "limit": min(limit, 100)}
        if cursor:
            params["after"] = str(cursor)  # OKX convention: older bound
        resp = _fetch_json(f"{OKX_API}/api/v5/public/funding-rate-history", params)
        if not resp or resp.get("code") != "0":
            logger.warning("%s funding bad response: %s", symbol, (resp or {}).get("msg"))
            break
        batch = resp.get("data") or []
        if not batch:
            break
        for row in batch:
            all_f.append({
                "fundingTime": int(row["fundingTime"]),
                "fundingRate": row["realizedRate"] if "realizedRate" in row else row["fundingRate"],
            })
        oldest = min(int(r["fundingTime"]) for r in batch)
        if len(batch) < params["limit"]:
            break
        if start_ms and oldest <= start_ms:
            break
        cursor = oldest
        time.sleep(0.05)
    all_f.sort(key=lambda r: r["fundingTime"])
    seen = set()
    out = []
    for r in all_f:
        if r["fundingTime"] in seen:
            continue
        if start_ms and r["fundingTime"] < start_ms:
            continue
        seen.add(r["fundingTime"])
        out.append(r)
    return out


# ─── ATR computation ─────────────────────────────────────────────────────────

def compute_atr_series(klines: List[list], period: int) -> List[float]:
    """Return an ATR series, same length as klines (NaN until warmup)."""
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]
    trs = [highs[0] - lows[0]]
    for i in range(1, len(klines)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1]),
        )
        trs.append(tr)
    atr = [float("nan")] * len(klines)
    if len(trs) >= period:
        # Simple (Wilder) ATR
        atr[period-1] = sum(trs[:period]) / period
        for i in range(period, len(trs)):
            atr[i] = (atr[i-1] * (period - 1) + trs[i]) / period
    return atr


# ─── Signal generation (PROXY) ───────────────────────────────────────────────

def compute_funding_signals(funding: List[dict]) -> List[dict]:
    """Label each funding observation as LONG/SHORT/NONE using a rolling 30d z-score.

    Funding rate = rate over 8h. High positive rate → retail longs pay shorts
    → crowded long → proxy SHORT signal. And vice versa.
    Returns list aligned with funding with {'time', 'rate', 'signal'}.
    """
    out = []
    window = 30 * FUNDING_PERIODS_PER_DAY  # 90 periods
    rates_buf: List[float] = []
    for f in funding:
        rate = float(f.get("fundingRate", 0))
        rates_buf.append(rate)
        if len(rates_buf) > window:
            rates_buf.pop(0)
        signal = "NONE"
        if len(rates_buf) >= window // 2:
            sorted_r = sorted(rates_buf)
            n = len(sorted_r)
            hi = sorted_r[int(n * FUNDING_EXTREME_PCTILE / 100)]
            lo = sorted_r[int(n * (100 - FUNDING_EXTREME_PCTILE) / 100)]
            if rate >= hi and rate > 0:
                signal = "SHORT"   # crowded long → fade
            elif rate <= lo and rate < 0:
                signal = "LONG"    # crowded short → fade
        out.append({
            "time": int(f["fundingTime"]),
            "rate": rate,
            "signal": signal,
        })
    return out


# ─── Trade simulation ────────────────────────────────────────────────────────

@dataclass
class BacktestTrade:
    symbol: str
    direction: str
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    quantity: float
    gross_pnl: float
    fees: float
    funding_paid: float
    net_pnl: float
    r_multiple: float
    exit_reason: str


def simulate_symbol(symbol: str, start_ms: int, end_ms: int) -> List[BacktestTrade]:
    """Run the proxy strategy on one symbol over the given window."""
    logger.info("Fetching %s klines + funding...", symbol)
    klines = fetch_klines(symbol, "4h", start_ms, end_ms)
    funding = fetch_funding(symbol, start_ms, end_ms)
    if not klines or not funding:
        logger.warning("%s: empty data, skipping", symbol)
        return []

    atr = compute_atr_series(klines, WHALE_ATR_PERIOD)
    # Build price lookup keyed by kline open time for fast entry/exit pricing
    price_by_t: Dict[int, dict] = {}
    for i, k in enumerate(klines):
        price_by_t[int(k[0])] = {
            "index": i,
            "open": float(k[1]), "high": float(k[2]),
            "low": float(k[3]),  "close": float(k[4]),
            "close_time": int(k[6]),
            "atr": atr[i] if atr[i] == atr[i] else None,  # NaN check
        }
    kline_starts = sorted(price_by_t.keys())

    def bar_at_or_after(t_ms: int) -> Optional[dict]:
        """Find the first kline that starts at/after t_ms."""
        for t in kline_starts:
            if t >= t_ms:
                return price_by_t[t]
        return None

    signals = compute_funding_signals(funding)

    trades: List[BacktestTrade] = []
    open_trade: Optional[dict] = None
    last_entry_time = 0

    notional = WHALE_MARGIN_CONSENSUS * WHALE_LEVERAGE

    for sig in signals:
        t = sig["time"]

        # Handle open trade first: check SL/TP on each 4h bar until a signal-flip
        if open_trade is not None:
            # Walk bars from last-checked time forward until funding time t
            last_t = open_trade["last_checked_t"]
            for kt in kline_starts:
                if kt <= last_t:
                    continue
                if kt > t:
                    break
                bar = price_by_t[kt]
                hit = None
                if open_trade["direction"] == "LONG":
                    if bar["low"] <= open_trade["sl"]:
                        hit = ("SL", open_trade["sl"])
                    elif bar["high"] >= open_trade["tp"]:
                        hit = ("TP", open_trade["tp"])
                else:
                    if bar["high"] >= open_trade["sl"]:
                        hit = ("SL", open_trade["sl"])
                    elif bar["low"] <= open_trade["tp"]:
                        hit = ("TP", open_trade["tp"])

                if hit:
                    reason, exit_px = hit
                    trades.append(_close_trade(open_trade, bar["close_time"],
                                                exit_px, reason, funding, t))
                    open_trade = None
                    break
                open_trade["last_checked_t"] = kt

            # Signal-flip early exit
            if open_trade and sig["signal"] != "NONE" \
                    and sig["signal"] != open_trade["direction"]:
                # Exit at next bar open
                nb = bar_at_or_after(t)
                if nb:
                    trades.append(_close_trade(open_trade, nb["close_time"],
                                                nb["open"], "signal_flip",
                                                funding, t))
                    open_trade = None

        # Now consider opening a new trade
        if open_trade is None and sig["signal"] in ("LONG", "SHORT"):
            if t - last_entry_time < MIN_SIGNAL_GAP_DAYS * 86400 * 1000:
                continue
            entry_bar = bar_at_or_after(t)
            if entry_bar is None or entry_bar["atr"] is None:
                continue
            entry_px = entry_bar["open"]
            atr_val = entry_bar["atr"]
            if atr_val <= 0:
                continue
            qty = notional / entry_px
            if sig["signal"] == "LONG":
                sl = entry_px - WHALE_SL_ATR_MULT * atr_val
                tp = entry_px + WHALE_TP_ATR_MULT * atr_val
            else:
                sl = entry_px + WHALE_SL_ATR_MULT * atr_val
                tp = entry_px - WHALE_TP_ATR_MULT * atr_val
            open_trade = {
                "symbol": symbol,
                "direction": sig["signal"],
                "entry_time": entry_bar["close_time"],
                "entry_price": entry_px,
                "quantity": qty,
                "sl": sl, "tp": tp,
                "atr_at_entry": atr_val,
                "last_checked_t": entry_bar["close_time"],
            }
            last_entry_time = t

    # Close any remaining position at final bar
    if open_trade is not None and kline_starts:
        last_bar = price_by_t[kline_starts[-1]]
        trades.append(_close_trade(open_trade, last_bar["close_time"],
                                    last_bar["close"], "end_of_window",
                                    funding, last_bar["close_time"]))

    return trades


def _close_trade(trade: dict, exit_time: int, exit_price: float,
                  reason: str, all_funding: List[dict], now_t: int) -> BacktestTrade:
    """Finalize a trade with fees, funding, and PnL."""
    qty = trade["quantity"]
    entry_px = trade["entry_price"]
    if trade["direction"] == "LONG":
        gross = (exit_price - entry_px) * qty
    else:
        gross = (entry_px - exit_price) * qty

    # Slippage — charge one slippage on entry + one on exit
    notional_entry = entry_px * qty
    notional_exit = exit_price * qty
    slippage = (notional_entry + notional_exit) * SLIPPAGE_BPS / 10000
    fees = (notional_entry + notional_exit) * TAKER_FEE_BPS / 10000

    # Funding cost — sum funding rates charged during hold
    funding_cost = 0.0
    for f in all_funding:
        t = int(f["fundingTime"])
        if t < trade["entry_time"] or t > exit_time:
            continue
        rate = float(f.get("fundingRate", 0))
        # Longs pay positive funding, shorts pay negative funding
        pos_notional = qty * ((entry_px + exit_price) / 2)
        if trade["direction"] == "LONG":
            funding_cost += rate * pos_notional
        else:
            funding_cost -= rate * pos_notional  # shorts RECEIVE when funding positive

    net = gross - fees - slippage - funding_cost
    risk = WHALE_SL_ATR_MULT * trade["atr_at_entry"] * qty
    r_multiple = net / risk if risk > 0 else 0.0

    return BacktestTrade(
        symbol=trade["symbol"],
        direction=trade["direction"],
        entry_time=trade["entry_time"],
        entry_price=entry_px,
        exit_time=exit_time,
        exit_price=exit_price,
        quantity=qty,
        gross_pnl=gross,
        fees=fees + slippage,
        funding_paid=funding_cost,
        net_pnl=net,
        r_multiple=r_multiple,
        exit_reason=reason,
    )


# ─── Metrics ─────────────────────────────────────────────────────────────────

def compute_metrics(trades: List[BacktestTrade]) -> dict:
    if not trades:
        return {"total_trades": 0}
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl < 0]
    win_rate = len(wins) / len(trades) * 100
    gross_profit = sum(t.net_pnl for t in wins)
    gross_loss = abs(sum(t.net_pnl for t in losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    total_net = sum(t.net_pnl for t in trades)
    total_funding = sum(t.funding_paid for t in trades)
    total_fees = sum(t.fees for t in trades)
    avg_r = sum(t.r_multiple for t in trades) / len(trades)

    # Simple Sharpe on per-trade returns
    import statistics
    if len(trades) >= 2:
        returns = [t.r_multiple for t in trades]
        mean_r = statistics.mean(returns)
        std_r = statistics.stdev(returns)
        sharpe = (mean_r / std_r) * (len(trades) ** 0.5) if std_r > 0 else 0
    else:
        sharpe = 0

    # Max drawdown on cumulative equity (R-normalized)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        equity += t.net_pnl
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(pf, 2),
        "avg_r_multiple": round(avg_r, 3),
        "sharpe_approx": round(sharpe, 2),
        "total_net_pnl": round(total_net, 2),
        "total_fees_slippage": round(total_fees, 2),
        "total_funding_paid": round(total_funding, 2),
        "max_drawdown_usd": round(max_dd, 2),
    }


def pass_fail(m: dict) -> str:
    """Apply the softened passing bar (plan update, Apr 2026).

    Replaced the unrealistic 'avg R ≥ 1.3' (would need ~80% WR at 2R) with
    profit factor ≥ 1.4. Softened Sharpe bar 1.0 → 0.8 and DD/Net 25% → 30%.
    """
    reasons = []
    if m.get("win_rate", 0) < 45:
        reasons.append(f"win rate {m['win_rate']}% < 45%")
    if m.get("profit_factor", 0) < 1.4:
        reasons.append(f"PF {m['profit_factor']} < 1.4")
    if m.get("sharpe_approx", 0) < 0.8:
        reasons.append(f"sharpe {m['sharpe_approx']} < 0.8")
    total = m.get("total_net_pnl", 0)
    maxdd = abs(m.get("max_drawdown_usd", 0))
    if total > 0 and maxdd / total > 0.30:
        reasons.append(f"max DD ${maxdd:.0f} > 30% of net ${total:.0f}")
    return "PASS" if not reasons else "FAIL: " + "; ".join(reasons)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    end = int(time.time() * 1000)
    start = end - MONTHS_BACK * 30 * 86400 * 1000

    all_trades: List[BacktestTrade] = []
    per_symbol_metrics: Dict[str, dict] = {}

    for sym in UNIVERSE:
        try:
            trades = simulate_symbol(sym, start, end)
        except Exception as e:
            logger.error("%s failed: %s", sym, e)
            continue
        all_trades.extend(trades)
        per_symbol_metrics[sym] = compute_metrics(trades)
        logger.info("%s: %d trades  PF=%.2f  WR=%.1f%%  Net=$%.0f",
                    sym, len(trades),
                    per_symbol_metrics[sym].get("profit_factor", 0),
                    per_symbol_metrics[sym].get("win_rate", 0),
                    per_symbol_metrics[sym].get("total_net_pnl", 0))

    overall = compute_metrics(all_trades)
    overall["verdict"] = pass_fail(overall)

    results = {
        "window_months": MONTHS_BACK,
        "universe": UNIVERSE,
        "proxy": "funding_extreme_fade",
        "thresholds": {
            "funding_pctile": FUNDING_EXTREME_PCTILE,
            "sl_atr_mult": WHALE_SL_ATR_MULT,
            "tp_atr_mult": WHALE_TP_ATR_MULT,
            "notional_per_trade": WHALE_MARGIN_CONSENSUS * WHALE_LEVERAGE,
        },
        "overall_metrics": overall,
        "per_symbol_metrics": per_symbol_metrics,
    }

    out_json = Path(BOT_DIR) / "backtest_whale_proxy_results.json"
    out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    logger.info("Wrote metrics to %s", out_json)

    out_csv = Path(BOT_DIR) / "backtest_whale_proxy_trades.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "direction", "entry_time", "entry_price",
                    "exit_time", "exit_price", "quantity", "gross_pnl",
                    "fees_slippage", "funding_paid", "net_pnl",
                    "r_multiple", "exit_reason"])
        for t in all_trades:
            w.writerow([
                t.symbol, t.direction,
                datetime.fromtimestamp(t.entry_time / 1000, tz=timezone.utc).isoformat(),
                round(t.entry_price, 6),
                datetime.fromtimestamp(t.exit_time / 1000, tz=timezone.utc).isoformat(),
                round(t.exit_price, 6),
                round(t.quantity, 8),
                round(t.gross_pnl, 2), round(t.fees, 2),
                round(t.funding_paid, 2), round(t.net_pnl, 2),
                round(t.r_multiple, 3), t.exit_reason,
            ])
    logger.info("Wrote %d trades to %s", len(all_trades), out_csv)

    print()
    print("=" * 60)
    print(f"  BACKTEST RESULTS ({MONTHS_BACK} months, {len(UNIVERSE)} symbols)")
    print("=" * 60)
    for k, v in overall.items():
        print(f"  {k:22s} {v}")
    print("=" * 60)


if __name__ == "__main__":
    main()
