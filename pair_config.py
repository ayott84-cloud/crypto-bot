"""ETH/BTC pair-trade bot configuration (Phase F).

Mean-reversion strategy on the 30-day rolling z-score of ETH/BTC price
ratio. Net dollar-neutral by construction: same notional on both legs.

Defaults follow plan F + the public-pattern cointegration research:
  - 30-day rolling window, |z| ≥ 2 entry, |z| ≤ 0.5 exit
  - 5-day max hold (mean reversion is fast or doesn't happen)
  - Z-stop at 2 × entry threshold (z ≤ -4 for LONG_ETH side)
  - Default PAUSED until backtest + 30 days paper validate
"""

from __future__ import annotations

import os
from pathlib import Path

_BOT_DIR = Path(__file__).resolve().parent

# ─── Master pause flag ─────────────────────────────────────────────────────
PAIR_PAUSED = os.getenv("PAIR_PAUSED", "true").lower() in ("true", "1", "yes")

# ─── Polling cadence ───────────────────────────────────────────────────────
# Daily timeframe (Bitsilk research) — poll every 5 minutes to react quickly
# to z reversion, but indicators advance on the 1d kline cadence.
PAIR_POLL_INTERVAL_SECONDS = 300
PAIR_INTERVAL = "1d"

# ─── State + naming ────────────────────────────────────────────────────────
PAIR_STATE_KEY_PREFIX = "PAIR_"
PAIR_STRATEGY_TAG = "Pair"
PAIR_HEARTBEAT_FILE = _BOT_DIR / ".pair_heartbeat"

# State keys identify each leg of the pair:
#   PAIR_ETHBTC_LONG_LEG   — the long-the-cheap-asset leg
#   PAIR_ETHBTC_SHORT_LEG  — the short-the-rich-asset leg
PAIR_LONG_LEG_KEY  = f"{PAIR_STATE_KEY_PREFIX}ETHBTC_LONG_LEG"
PAIR_SHORT_LEG_KEY = f"{PAIR_STATE_KEY_PREFIX}ETHBTC_SHORT_LEG"

# ─── Position sizing ───────────────────────────────────────────────────────
# $50 margin × 10x = $500 notional per leg → $1000 gross exposure, $0 net.
PAIR_MARGIN_PER_LEG = 50.0
PAIR_LEVERAGE = 10
# Only one pair at a time (consumes 2 of 8 MAX_POSITIONS slots)
MAX_PAIR_POSITIONS = 1

# ─── Pair definition ───────────────────────────────────────────────────────
# ETH and BTC perp symbols on WEEX
PAIR_LONG_SYMBOL  = "ETHUSDT"   # the "rich/cheap" alt
PAIR_SHORT_SYMBOL = "BTCUSDT"   # the reference

# ─── Signal parameters ─────────────────────────────────────────────────────
PAIR_CONFIG = {
    "z_window":       30,   # rolling lookback (days at 1d timeframe)
    "entry_z":        2.0,  # |z| ≥ 2 → enter
    "exit_z":         0.5,  # |z| ≤ 0.5 → close (reverted)
    "max_hold_bars":  5,    # 5 days max hold
    "atr_stop_mult":  2.0,  # |z| past entry_z × this → adverse stop
}


# ─── Phase K — pair candidate configs (NOT live until promoted) ──────────
# Each entry describes one mean-reversion candidate pair. Same strategy
# logic (z-score on the ratio); only the symbols differ.
#
# Cointegration is asset-specific — ETH/BTC works because they share macro
# drivers (BTC dominance, ETH/BTC ratio mean-reversion). For other pairs:
#   BTC/SOL — both large-cap but SOL has its own L1 cycles → weaker
#             cointegration than ETHBTC. Worth checking.
#   ETH/SOL — both smart-contract L1s, sometimes mean-revert against each
#             other on rotation flows. Plausible.
#   BTC/LTC — historical correlation, "digital silver vs gold" narrative.
#             Lower frequency but cleaner long-term cointegration.
# tools/validate_pair_candidates.py runs each cfg through replay_pair
# and gates against PF≥1.3, n≥5, DD≤20%. Pair-specific thresholds since
# spread strategies typically have lower PF than directional ones.
PAIR_CANDIDATE_CONFIGS = {
    "BTCSOL": {
        "long_symbol":  "BTCUSDT",
        "short_symbol": "SOLUSDT",
        "interval":     "1d",
        "cfg":          dict(PAIR_CONFIG),
    },
    "ETHSOL": {
        "long_symbol":  "ETHUSDT",
        "short_symbol": "SOLUSDT",
        "interval":     "1d",
        "cfg":          dict(PAIR_CONFIG),
    },
    "BTCLTC": {
        "long_symbol":  "BTCUSDT",
        "short_symbol": "LTCUSDT",
        "interval":     "1d",
        "cfg":          dict(PAIR_CONFIG),
    },
    # ETHBTC short-TF variant (4H) — same cointegration story but faster
    # reversion cycles. Tighter z_window matches the bar count.
    "ETHBTC_4H": {
        "long_symbol":  "ETHUSDT",
        "short_symbol": "BTCUSDT",
        "interval":     "4h",
        "cfg":          {**PAIR_CONFIG, "z_window": 30, "max_hold_bars": 30},
    },
}


# ─── Phase J.6 — backtest stats for projection table ──────────────────────
# Source: tools/backtest_replay.py 1000-bar 1d replay (Jun 2026). n=42
# trades over 2.63yr — highest-confidence row in the projection.
PAIR_BACKTEST_STATS = {
    "ETHBTC": {"pf": 4.96, "trades": 42, "pnl_pct": 227.2, "dd_pct": 20.7,
                "wr": 76.2, "years": 2.63,
                "source": "1000-bar 1d replay"},
}
