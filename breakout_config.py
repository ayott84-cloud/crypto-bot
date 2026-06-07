"""Donchian breakout bot configuration (Phase G).

Donchian breakout fills the silent-momentum gap: when BTC isn't trending
and the EMA-crossover momentum bot stands aside, individual alts can
still print clean N-bar highs/lows with strong ADX. Different signal
events than the momentum bot — diversifies the fleet.

Defaults follow plan G.1 + the public-pattern backtest research:
  - Donchian 20 on 4h / Daily timeframes
  - SHORT off by default; flip per asset after backtest validates
  - PAUSED by default until backtest + 60 days paper validate
"""

from __future__ import annotations

import os
from pathlib import Path

_BOT_DIR = Path(__file__).resolve().parent

# ─── Master pause flag ─────────────────────────────────────────────────────
# Set BREAKOUT_PAUSED=false in .env after backtest + paper validation.
# Defaults TRUE so deploying the code never opens a live position
# accidentally.
BREAKOUT_PAUSED = os.getenv("BREAKOUT_PAUSED", "true").lower() in ("true", "1", "yes")

# ─── Polling cadence ───────────────────────────────────────────────────────
# Same as momentum: 5-minute poll. Each asset's own timeframe (4h, 1d)
# governs how often a real signal CAN fire.
BREAKOUT_POLL_INTERVAL_SECONDS = 300

# ─── State + naming ────────────────────────────────────────────────────────
BREAKOUT_STATE_KEY_PREFIX = "BREAKOUT_"
BREAKOUT_STRATEGY_TAG = "Breakout"  # for log_trade + dashboard bot column

# Heartbeat file the dashboard checks for LIVE/STALE/NEVER pill.
BREAKOUT_HEARTBEAT_FILE = _BOT_DIR / ".breakout_heartbeat"

# ─── Position sizing + caps ────────────────────────────────────────────────
# Half-size during validation per plan F/G pattern. Lift after paper PF >= 1.5.
BREAKOUT_MARGIN_PER_TRADE = 25.0
BREAKOUT_LEVERAGE = 10
# Max concurrent breakout positions. Consumes slots out of the global
# MAX_POSITIONS pool (currently 8); cap at 2 during validation.
MAX_BREAKOUT_POSITIONS = 2

# ─── Per-asset configs ─────────────────────────────────────────────────────
# Defaults below are the plan G baseline. Override per asset once
# TradingView backtest validates. Add/remove assets here as needed.
BREAKOUT_ASSETS = {
    "BTC_4H": {
        "symbol":               "BTCUSDT",
        "interval":             "4h",
        "donchian_period":      55,   # G.2: Turtle System 1 (was 20)
        "donchian_exit_period": 20,   # G.2: Turtle exit (was 10)
        "atr_period":           14,
        "atr_sma_period":       20,
        "adx_period":           14,
        "adx_threshold":        20,    # entry gate
        "adx_exit_threshold":   15,    # exit trigger (trend dying)
        "sl_atr_mult":          2.5,
        "use_volume_filter":     True,    # G.2 — require >1.5x SMA volume
        "volume_threshold_mult": 1.5,
        "volume_sma_period":     20,
        "use_trend_filter":      True,    # G.2 — require 1D EMA20/50 agree   # widened from 1.5 after 2C.3 backtest showed noise-stop-outs
        "allow_short":          True,    # G.2: enabled — strategy is symmetric, restricting to LONG-only kills it in downtrends  # off until per-asset backtest
        "sl_atr_mult_short":    1.0,    # tighter SL for shorts
        "strategy_name":        "BTC 4H Breakout",
        "use_btc_filter":       False,  # breakout IS the directional signal
    },
    "ETH_4H": {
        "symbol":               "ETHUSDT",
        "interval":             "4h",
        "donchian_period":      55,   # G.2: Turtle System 1 (was 20)
        "donchian_exit_period": 20,   # G.2: Turtle exit (was 10)
        "atr_period":           14,
        "atr_sma_period":       20,
        "adx_period":           14,
        "adx_threshold":        20,
        "adx_exit_threshold":   15,
        "sl_atr_mult":          2.5,
        "use_volume_filter":     True,    # G.2 — require >1.5x SMA volume
        "volume_threshold_mult": 1.5,
        "volume_sma_period":     20,
        "use_trend_filter":      True,    # G.2 — require 1D EMA20/50 agree   # widened from 1.5 after 2C.3 backtest showed noise-stop-outs
        "allow_short":          True,    # G.2: enabled — strategy is symmetric, restricting to LONG-only kills it in downtrends
        "sl_atr_mult_short":    1.0,
        "strategy_name":        "ETH 4H Breakout",
        "use_btc_filter":       False,
    },
    "SOL_4H": {
        "symbol":               "SOLUSDT",
        "interval":             "4h",
        "donchian_period":      55,   # G.2: Turtle System 1 (was 20)
        "donchian_exit_period": 20,   # G.2: Turtle exit (was 10)
        "atr_period":           14,
        "atr_sma_period":       20,
        "adx_period":           14,
        "adx_threshold":        20,
        "adx_exit_threshold":   15,
        "sl_atr_mult":          2.5,
        "use_volume_filter":     True,    # G.2 — require >1.5x SMA volume
        "volume_threshold_mult": 1.5,
        "volume_sma_period":     20,
        "use_trend_filter":      True,    # G.2 — require 1D EMA20/50 agree   # widened from 1.5 after 2C.3 backtest showed noise-stop-outs
        "allow_short":          True,    # G.2: enabled — strategy is symmetric, restricting to LONG-only kills it in downtrends
        "sl_atr_mult_short":    1.0,
        "strategy_name":        "SOL 4H Breakout",
        "use_btc_filter":       False,
    },
}


# ─── Phase J.6 — backtest stats for projection table ──────────────────────
# Source: tools/backtest_replay.py 1000-bar 4h replay (Jun 2026). Each
# row's `trades` count is genuinely small; the projection table renders
# these as "low confidence" so the operator doesn't over-weight them.
BREAKOUT_BACKTEST_STATS = {
    "BTC_4H": {"pf": 2.81, "trades":  4, "pnl_pct": 11.5, "dd_pct": 6.0,
                "wr": 50.0, "years": 0.46,
                "source": "1000-bar 4h replay (small n)"},
    "ETH_4H": {"pf": 3.17, "trades":  3, "pnl_pct": 21.5, "dd_pct": 9.9,
                "wr": 66.7, "years": 0.46,
                "source": "1000-bar 4h replay (small n)"},
    "SOL_4H": {"pf": 91.83, "trades": 2, "pnl_pct": 23.5, "dd_pct": 0.3,
                "wr": 50.0, "years": 0.46,
                "source": "1000-bar 4h replay (n=2 — directional only)"},
}
