"""Whale-tracking bot configuration.

All knobs specific to the Hyperliquid-whale strategy live here.
The bot-1 momentum config is untouched — see config.py for shared constants
(DRY_RUN, MAX_POSITIONS, DEFAULT_LEVERAGE, STATE_FILE, JOURNAL_FILE, etc.).
"""

from __future__ import annotations

import os
from pathlib import Path

# ─── Polling ─────────────────────────────────────────────────────────────────
WHALE_POLL_INTERVAL_SECONDS = 15 * 60       # 15 minutes
WHALE_FETCH_COUNT = 20                        # top N and rekt N wallets to scan
WHALE_RETRY_BACKOFF_SECONDS = [5, 10, 30]     # exponential retry on HL API failure

# ─── Capital / sizing ────────────────────────────────────────────────────────
# Whale bot shares the global 8-slot cap with bot 1 (first-come-first-served).
WHALE_MARGIN_CONSENSUS = 50.0       # $ margin for consensus trades
WHALE_MARGIN_DIVERGENCE = 75.0      # $ margin for divergence (1.5x size)
WHALE_LEVERAGE = 10                 # matches bot 1

# ─── Signal thresholds ───────────────────────────────────────────────────────
# Tightened after 24mo proxy backtest (Apr 2026): aggregate PF 1.20, WR 42.5%.
# Per-symbol spread was wide; tightening improves quality at the cost of fewer trades.
MIN_SMART_TRADERS_PER_COIN = 7      # was 5 — coin needs at least this many top-20 traders taking a position
CONSENSUS_LONG_PCT = 85             # was 80 — smart_long_pct >= this → CONSENSUS_LONG
CONSENSUS_SHORT_PCT = 85            # was 80 — smart_short_pct >= this → CONSENSUS_SHORT
DIVERGENCE_LONG_PCT = 75            # was 70 — smart_long_pct >= this AND rekt_short_pct >= this → DIVERGENCE_LONG
DIVERGENCE_SHORT_PCT = 75           # was 70 — smart_short_pct >= this AND rekt_long_pct >= this → DIVERGENCE_SHORT
CROWDED_TRADE_PCT = 70              # if BOTH smart AND rekt agree at this level, skip (crowded)

# Edge-decay guard: require smart money is currently winning on this coin
# (sum of unrealized PnL across smart-money positions in this coin must be positive).
REQUIRE_SMART_WINNING = True

# Signal-flip exit: if smart-money dominant_pct drops below this on next scan, exit.
SIGNAL_FLIP_THRESHOLD = 55

# Per-coin cooldown after exit (prevents re-entry churn).
WHALE_COOLDOWN_HOURS = 24

# ─── Risk management ─────────────────────────────────────────────────────────
# Stop-loss and take-profit are expressed in ATR multiples.
# ATR is computed on 4H bars fetched from WEEX.
WHALE_ATR_PERIOD = 14
WHALE_ATR_INTERVAL = "4h"
WHALE_SL_ATR_MULT = 1.5
WHALE_TP_ATR_MULT = 3.0              # 2R reward:risk

# Soft kill-switch: if whale-bot loses more than this in a rolling 7-day window,
# pause new entries (existing positions still run their SL/TP).
WHALE_MAX_7D_LOSS_USD = 500.0

# ─── WEEX symbol whitelist ───────────────────────────────────────────────────
# On startup, the whale bot pulls all WEEX contract symbols and caches them.
# Whale signals for coins not in the whitelist are dropped before signaling.
# This cache file avoids re-hitting the API every poll.
_BOT_DIR = Path(__file__).resolve().parent
WHALE_SYMBOL_WHITELIST_CACHE = _BOT_DIR / ".whale_weex_symbols.json"
WHALE_SYMBOL_CACHE_TTL_HOURS = 24

# ─── Top-N market cap universe filter ────────────────────────────────────────
# Fetched from CoinGecko once per day. Filters out illiquid long-tail coins
# while preserving reactivity to whatever whales currently trade at size.
# Dynamic — refreshes daily. Set RANK_LIMIT to 200+ if too restrictive in practice.
WHALE_UNIVERSE_CG_URL = "https://api.coingecko.com/api/v3/coins/markets"
WHALE_TOP100_CACHE = _BOT_DIR / ".whale_top100.json"
WHALE_TOP100_TTL_HOURS = 24
WHALE_MARKETCAP_RANK_LIMIT = 100

# HL coin names sometimes have a "k" prefix (kPEPE, kLUNC, kNEIRO, kBONK) meaning
# "price quoted per 1000 tokens". Map these to their WEEX equivalent (without k).
HL_TO_WEEX_SYMBOL_OVERRIDES = {
    "kPEPE": "PEPEUSDT",
    "kLUNC": "LUNCUSDT",
    "kNEIRO": "NEIROUSDT",
    "kBONK": "BONKUSDT",
    "kSHIB": "SHIBUSDT",
    "kFLOKI": "FLOKIUSDT",
}

# ─── State/journal keys ──────────────────────────────────────────────────────
WHALE_STATE_KEY_PREFIX = "WHALE_"   # state keys like "WHALE_BTC", "WHALE_ETH"
WHALE_STRATEGY_TAG = "Whale Track"  # prefix in journal — e.g. "Whale Track BTC LONG"

# ─── Logging / diagnostics ───────────────────────────────────────────────────
WHALE_SIGNAL_LOG = _BOT_DIR / "whale_signals.jsonl"  # append every poll's signals
WHALE_DEBUG_DUMP = os.getenv("WHALE_DEBUG_DUMP", "false").lower() in ("1", "true", "yes")
