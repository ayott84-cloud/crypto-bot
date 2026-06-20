"""Phase M — scalp bot configuration (5-minute volatility-expansion breakouts).

A 1:2 R/R fixed-bracket strategy on the 5m chart:
  - Vol expansion: SMA(range, 10) > SMA(range, 50)
  - Momentum: close > close[20 bars ago] (mirrored for SHORT)
  - Breakout: close > max(close[-22:-2]) (mirrored for SHORT)
  - Body color: green for LONG, red for SHORT
  - Exits: SL -1.5%, TP +3%, per-asset 10-min re-entry cooldown

Initial deployment (Phase M.6 decision): BTC_5M, ETH_5M, SOL_5M only
for 14 days. Remaining top-10 stage as candidates and get promoted
after live-paper PF ≥ 1.3 at the day-14 review.

Sizing: $10 margin × 10x = $100 notional, half of breakout's $25 stake.
More frequent trades, smaller stakes per the scalping risk profile.
"""

from __future__ import annotations

import os
from pathlib import Path

_BOT_DIR = Path(__file__).resolve().parent

# ─── Master pause flag ─────────────────────────────────────────────────
# Defaults TRUE so deploying the code never opens a live position
# accidentally. Flip via `SCALP_PAUSED=false` in .env once paper backtest
# clears the gates.
SCALP_PAUSED = os.getenv("SCALP_PAUSED", "true").lower() in ("true", "1", "yes")

# ─── Polling cadence ───────────────────────────────────────────────────
# Poll every minute; the strategy only acts on FRESHLY CLOSED 5m bars.
# We still need sub-minute responsiveness for SL/TP exit management on
# active positions (price can blow through both within seconds in fast
# moves), so the poll interval is much shorter than other bots' 5 min.
SCALP_POLL_INTERVAL_SECONDS = 60
SCALP_INTERVAL = "5m"

# ─── State + naming ────────────────────────────────────────────────────
SCALP_STATE_KEY_PREFIX = "SCALP_"
SCALP_STRATEGY_TAG = "Scalp"
SCALP_HEARTBEAT_FILE = _BOT_DIR / ".scalp_heartbeat"
SCALP_SIGNAL_LOG     = _BOT_DIR / "scalp_signals.jsonl"

# ─── Position sizing + caps ────────────────────────────────────────────
# Half of breakout's $25 — scalping needs more frequent trades at smaller
# stake size so a single bad signal can't bleed the account materially.
SCALP_MARGIN_PER_TRADE = 10.0
SCALP_LEVERAGE = 10
# 3 concurrent positions × $100 notional = $300 gross max. Per-trade
# downside capped at $100 × 1.5% = $1.50 SL hit. Even all-three losing
# simultaneously costs $4.50.
MAX_SCALP_POSITIONS = 3

# Per-asset re-entry cooldown (seconds). Prevents whipsaw chains where a
# stopped-out signal immediately re-arms. 10 min = roughly 2 fresh 5m
# candles, enough for conditions to genuinely re-establish.
SCALP_COOLDOWN_SECONDS = 600

# ─── Per-asset baseline (factory) ──────────────────────────────────────

def _scalp_default(symbol: str, name: str) -> dict:
    """Build the baseline 5m scalp config for one asset."""
    return {
        "symbol":             symbol,
        "interval":           SCALP_INTERVAL,
        "range_short_sma":    10,
        "range_long_sma":     50,
        "momentum_lookback":  20,
        "new_high_lookback":  20,
        "sl_pct":             1.5,
        "tp_pct":             3.0,
        "allow_short":        True,
        # L.2 regime gate intentionally OFF for scalp — the classifier
        # uses Daily EMA20/50/200 which is far slower than the 5m
        # signal frequency. Revisit after observing whether the
        # strategy gets chopped in strong_down regimes.
        "use_regime_gate":    False,
        "use_btc_filter":     False,
        "strategy_name":      name,
    }


# ─── Live universe — Phase M.6 starts with BTC/ETH/SOL only ───────────
SCALP_ASSETS = {
    "BTC_5M": _scalp_default("BTCUSDT", "BTC 5m Scalp"),
    "ETH_5M": _scalp_default("ETHUSDT", "ETH 5m Scalp"),
    "SOL_5M": _scalp_default("SOLUSDT", "SOL 5m Scalp"),
}

# ─── Candidate universe — remaining top-10 ────────────────────────────
# Promoted to SCALP_ASSETS after the day-14 live-paper review per the
# Phase M.6 rubric (PF ≥ 1.3, ≥ 30 closed trades).
SCALP_CANDIDATE_ASSETS = {
    "BNB_5M":  _scalp_default("BNBUSDT",  "BNB 5m Scalp"),
    "XRP_5M":  _scalp_default("XRPUSDT",  "XRP 5m Scalp"),
    "ADA_5M":  _scalp_default("ADAUSDT",  "ADA 5m Scalp"),
    "DOGE_5M": _scalp_default("DOGEUSDT", "DOGE 5m Scalp"),
    "AVAX_5M": _scalp_default("AVAXUSDT", "AVAX 5m Scalp"),
    "LINK_5M": _scalp_default("LINKUSDT", "LINK 5m Scalp"),
    "TRX_5M":  _scalp_default("TRXUSDT",  "TRX 5m Scalp"),
}

# ─── Backtest stats for projection table (populated post-validator) ───
SCALP_BACKTEST_STATS: dict = {}
