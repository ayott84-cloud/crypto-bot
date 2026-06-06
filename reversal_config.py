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
    "BTC_1H": {
        "symbol":             "BTCUSDT",
        "interval":           "1h",
        "rsi_length":         15,
        "oversold":           15.0,   # relaxed from 10 for paper validation
        "overbought":         85.0,   # relaxed from 90 for paper validation
        "range_mult":         2.5,    # relaxed from 3.0 for paper validation
        "range_sma_length":   14,
        "close_position_pct": 0.30,
        "allow_long":         True,
        "allow_short":        True,
        # Exits (NOT in source — defaults for paper validation)
        "atr_length":         14,
        "sl_atr_mult":        1.5,    # SL at 1.5×ATR adverse
        "tp1_atr_mult":       1.0,    # TP1 at VWAP retrace ~ 1×ATR
        "tp2_r_mult":         1.5,    # TP2 at 1.5R
        "max_hold_bars":      24,     # time stop: 24h on 1h tf
        "strategy_name":      "BTC 1H Reversal",
    },
    "ETH_1H": {
        "symbol":             "ETHUSDT",
        "interval":           "1h",
        "rsi_length":         15,
        "oversold":           15.0,   # relaxed from 10 for paper validation
        "overbought":         85.0,   # relaxed from 90 for paper validation
        "range_mult":         2.5,    # relaxed from 3.0 for paper validation
        "range_sma_length":   14,
        "close_position_pct": 0.30,
        "allow_long":         True,
        "allow_short":        True,
        "atr_length":         14,
        "sl_atr_mult":        1.5,
        "tp1_atr_mult":       1.0,
        "tp2_r_mult":         1.5,
        "max_hold_bars":      24,
        "strategy_name":      "ETH 1H Reversal",
    },
    "SOL_1H": {
        "symbol":             "SOLUSDT",
        "interval":           "1h",
        "rsi_length":         15,
        "oversold":           15.0,   # relaxed from 10 for paper validation
        "overbought":         85.0,   # relaxed from 90 for paper validation
        "range_mult":         2.5,    # relaxed from 3.0 for paper validation
        "range_sma_length":   14,
        "close_position_pct": 0.30,
        "allow_long":         True,
        "allow_short":        True,
        "atr_length":         14,
        "sl_atr_mult":        1.5,
        "tp1_atr_mult":       1.0,
        "tp2_r_mult":         1.5,
        "max_hold_bars":      24,
        "strategy_name":      "SOL 1H Reversal",
    },
}
