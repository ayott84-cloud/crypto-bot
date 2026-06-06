"""RSI-VWAP Extreme Reversal bot configuration (Phase I).

Strategy source: Alex Carter Trading — RSI VWAP + Extreme Reversal
(Whop product, paywalled; spec extracted from "How It Works" + "Claude
Code Prompt" tabs by operator).

Catches intrabar exhaustion reversals — 3× range capitulation candles at
RSI extremes. Fills the gap our trend-following fleet sleeps through.

Defaults follow the verbatim spec where given (RSI length 15, oversold
10, overbought 90, range multiplier 3). Exit rules + sizing are NOT
specified in the source — sensible defaults documented in plan I.3
applied here.
"""

from __future__ import annotations

import os
from pathlib import Path

_BOT_DIR = Path(__file__).resolve().parent

# ─── Master pause flag ─────────────────────────────────────────────────────
REVERSAL_PAUSED = os.getenv("REVERSAL_PAUSED", "true").lower() in ("true", "1", "yes")

# ─── Polling cadence ───────────────────────────────────────────────────────
REVERSAL_POLL_INTERVAL_SECONDS = 300

# ─── State + naming ────────────────────────────────────────────────────────
REVERSAL_STATE_KEY_PREFIX = "REVERSAL_"
REVERSAL_STRATEGY_TAG = "Reversal"
REVERSAL_HEARTBEAT_FILE = _BOT_DIR / ".reversal_heartbeat"

# ─── Position sizing + caps ────────────────────────────────────────────────
REVERSAL_MARGIN_PER_TRADE = 25.0
REVERSAL_LEVERAGE = 10
MAX_REVERSAL_POSITIONS = 2

# Re-entry lockout (bars on entry timeframe) — prevents whipsaw chains
REVERSAL_REENTRY_LOCKOUT_BARS = 4

# ─── Per-asset configs ─────────────────────────────────────────────────────
# Signal defaults match the Whop source verbatim. Exit rules below
# (sl/tp/time stop) are sensible defaults filling in what the source
# did not specify — tune during paper validation.
REVERSAL_ASSETS = {
    "BTC_1D": {
        "symbol":             "BTCUSDT",
        "interval":           "1d",   # I.3: Daily — captures Mar 2020 / May 2021 / Nov 2022 / Aug 2024 capitulations
        "rsi_length":         5,    # I.4.1: scaled for Daily — RSI(15) on Daily = 15-day smoothing, too slow for cap events; RSI(5) reacts like RSI(15) does on 1h
        "rsi_source":         "close",  # I.4: peer-review fix — RSI(close) on Daily reacts to cap candles
        "oversold":           15.0,   # I.3: Daily — restore tighter band, real capitulations clear it   # relaxed from 10 for paper validation
        "overbought":         85.0,   # I.3: Daily — restore tighter band   # relaxed from 90 for paper validation
        "range_mult":         2.5,    # I.3: Daily — 2.5x is achievable on real cap days    # relaxed from 3.0 for paper validation
        "range_sma_length":   14,   # I.3: standard 14-bar SMA on Daily
        "close_position_pct": 0.30,
        "window_bars":        3,      # I.3: tighter window — Daily candles print clean      # I.2: allow conjunction within 3-bar window
        "allow_long":         True,
        "allow_short":        True,
        # Exits (NOT in source — defaults for paper validation)
        "atr_length":         14,
        "sl_atr_mult":        1.5,    # SL at 1.5×ATR adverse
        "tp1_atr_mult":       1.0,    # TP1 at VWAP retrace ~ 1×ATR
        "tp2_r_mult":         1.5,    # TP2 at 1.5R
        "max_hold_bars":      7,      # I.3: 7 days on Daily
        "strategy_name":      "BTC 1D Reversal",
    },
    "ETH_1D": {
        "symbol":             "ETHUSDT",
        "interval":           "1d",   # I.3: Daily — captures Mar 2020 / May 2021 / Nov 2022 / Aug 2024 capitulations
        "rsi_length":         5,    # I.4.1: scaled for Daily — RSI(15) on Daily = 15-day smoothing, too slow for cap events; RSI(5) reacts like RSI(15) does on 1h
        "rsi_source":         "close",  # I.4: peer-review fix — RSI(close) on Daily reacts to cap candles
        "oversold":           15.0,   # I.3: Daily — restore tighter band, real capitulations clear it   # relaxed from 10 for paper validation
        "overbought":         85.0,   # I.3: Daily — restore tighter band   # relaxed from 90 for paper validation
        "range_mult":         2.5,    # I.3: Daily — 2.5x is achievable on real cap days    # relaxed from 3.0 for paper validation
        "range_sma_length":   14,   # I.3: standard 14-bar SMA on Daily
        "close_position_pct": 0.30,
        "window_bars":        3,      # I.3: tighter window — Daily candles print clean      # I.2: allow conjunction within 3-bar window
        "allow_long":         True,
        "allow_short":        True,
        "atr_length":         14,
        "sl_atr_mult":        1.5,
        "tp1_atr_mult":       1.0,
        "tp2_r_mult":         1.5,
        "max_hold_bars":      7,
        "strategy_name":      "ETH 1D Reversal",
    },
    "SOL_1D": {
        "symbol":             "SOLUSDT",
        "interval":           "1d",   # I.3: Daily — captures Mar 2020 / May 2021 / Nov 2022 / Aug 2024 capitulations
        "rsi_length":         5,    # I.4.1: scaled for Daily — RSI(15) on Daily = 15-day smoothing, too slow for cap events; RSI(5) reacts like RSI(15) does on 1h
        "rsi_source":         "close",  # I.4: peer-review fix — RSI(close) on Daily reacts to cap candles
        "oversold":           15.0,   # I.3: Daily — restore tighter band, real capitulations clear it   # relaxed from 10 for paper validation
        "overbought":         85.0,   # I.3: Daily — restore tighter band   # relaxed from 90 for paper validation
        "range_mult":         2.5,    # I.3: Daily — 2.5x is achievable on real cap days    # relaxed from 3.0 for paper validation
        "range_sma_length":   14,   # I.3: standard 14-bar SMA on Daily
        "close_position_pct": 0.30,
        "window_bars":        3,      # I.3: tighter window — Daily candles print clean      # I.2: allow conjunction within 3-bar window
        "allow_long":         True,
        "allow_short":        True,
        "atr_length":         14,
        "sl_atr_mult":        1.5,
        "tp1_atr_mult":       1.0,
        "tp2_r_mult":         1.5,
        "max_hold_bars":      7,
        "strategy_name":      "SOL 1D Reversal",
    },
}
