"""Whale-tracking bot configuration.

All knobs specific to the Hyperliquid-whale strategy live here.
The bot-1 momentum config is untouched — see config.py for shared constants
(DRY_RUN, MAX_POSITIONS, DEFAULT_LEVERAGE, STATE_FILE, JOURNAL_FILE, etc.).
"""

from __future__ import annotations

import os
from pathlib import Path

# ─── Master pause flag ───────────────────────────────────────────────────────
# Set WHALE_PAUSED=true in .env to stop the whale bot from opening NEW positions.
# Existing positions still manage toward exit (SL/TP/signal-flip). Use this
# during validation periods or when shutting the bot down for re-tuning.
# Per peer-review (May 2026): whale bot is paused pending PnL bug fix +
# accumulation of ≥30 cleanly-tracked trades before re-validating thresholds.
WHALE_PAUSED = os.getenv("WHALE_PAUSED", "true").lower() in ("true", "1", "yes")

# ─── Polling ─────────────────────────────────────────────────────────────────
WHALE_POLL_INTERVAL_SECONDS = 15 * 60       # 15 minutes
WHALE_FETCH_COUNT = 20                        # top N and rekt N wallets to scan
WHALE_RETRY_BACKOFF_SECONDS = [5, 10, 30]     # exponential retry on HL API failure

# ─── Phase W.2.13 — Price-action entry trigger ──────────────────────────────
# The structural fix for the 12/14-SL-hit failure mode. Whale consensus
# is CONTEXT, not the trigger: after every other filter passes, the entry
# waits until the last completed 4h bar CONFIRMS direction (green close
# above prior high for LONG; red close below prior low for SHORT).
# Persistence state keeps the signal alive across polls, so a confirmation
# 1-2 bars later still enters — we stop buying tops the leaderboard
# already bought. Default ON. Disable via WHALE_USE_ENTRY_TRIGGER=false.
WHALE_USE_ENTRY_TRIGGER = (
    os.getenv("WHALE_USE_ENTRY_TRIGGER", "true").lower()
    in ("true", "1", "yes")
)


# ─── Phase W.E.2 — Arkham CEX-flow gate ─────────────────────────────────────
# When ON (and ARKHAM_API_KEY is set in .env), the whale entry path
# queries Arkham's /token/top_flow/{chain} for the candidate coin's
# 24h net entity flow. LONG entries blocked if top entities are
# net DISTRIBUTORS > $1M; SHORT entries blocked on net ACCUMULATION.
# Turns the lagging HL leaderboard signal into a leading-confirmation
# flow at the cost of one Arkham API call per entry candidate.
WHALE_USE_ARKHAM_FLOW_GATE = (
    os.getenv("WHALE_USE_ARKHAM_FLOW_GATE", "false").lower()
    in ("true", "1", "yes")
)
WHALE_ARKHAM_FLOW_THRESHOLD_USD = float(
    os.getenv("WHALE_ARKHAM_FLOW_THRESHOLD_USD", "1000000")
)


# ─── Phase W.E.1 — Curated wallet whitelist ─────────────────────────────────
# When this list is non-empty, fetch_cohorts() scans these wallets directly
# as the smart cohort, BYPASSING the HL leaderboard rank entirely. Operator
# hand-curates the list from wallets they trust (cross-referenced via
# Arkham entity linking, hyperliquid-whale-tracker reviews, etc.). Trades
# fewer wallets, but each with higher conviction — eliminates the survivor-
# ship bias of leaderboard sort. Leave empty to use the U.3 composite-sorted
# leaderboard.
#
# Operator may also override via WHALE_WALLET_WHITELIST env var: a
# comma-separated list of 0x... addresses.
_WHITELIST_ENV = os.getenv("WHALE_WALLET_WHITELIST", "").strip()
WHALE_WALLET_WHITELIST: list[str] = (
    [a.strip() for a in _WHITELIST_ENV.split(",") if a.strip()]
    if _WHITELIST_ENV else []
)


# ─── Cohort quality gates (Phase W.U.3 — eliminate survivorship bias) ───
# Raw HL leaderboard sorting by all-time PnL captures "lucky 3-month
# winners" whose recent performance has decayed. Two gates kill that:
#   - MIN_ACCOUNT_VALUE_USD: drops dust accounts (vaults, sub-accounts,
#     low-capital wallets whose "wins" don't move markets)
#   - REQUIRE_POSITIVE_MONTH_PNL: forces last-30d-positive — if the
#     wallet is currently bleeding, the all-time PnL is stale alpha
# Both default ON. Toggle via WHALE_COHORT_REQUIRE_POSITIVE_MONTH=false
# in .env to restore the legacy biased behavior.
MIN_ACCOUNT_VALUE_USD = 100_000.0
WHALE_COHORT_REQUIRE_POSITIVE_MONTH = (
    os.getenv("WHALE_COHORT_REQUIRE_POSITIVE_MONTH", "true").lower()
    in ("true", "1", "yes")
)

# ─── Capital / sizing ────────────────────────────────────────────────────────
# Whale bot shares the global 8-slot cap with bot 1 (first-come-first-served).
WHALE_MARGIN_CONSENSUS = 50.0       # $ margin for consensus trades
WHALE_MARGIN_DIVERGENCE = 75.0      # $ margin for divergence (1.5x size)
WHALE_LEVERAGE = 10                 # matches bot 1

# ─── Signal thresholds ───────────────────────────────────────────────────────
# Phase W.A (Jun 2026) re-tuning after −$225.67 / 12-of-14-SL-hit failure mode:
# - Tighter min-trader floor (more conviction required before a signal qualifies)
# - Slightly lower CONSENSUS pct (with the higher floor, absolute trader count
#   still goes up — same conviction, broader signal universe)
# - DIVERGENCE thresholds untouched (already strict, contributing few trades)
MIN_SMART_TRADERS_PER_COIN = 10     # W.A: 7 → 10 (stronger consensus required)
CONSENSUS_LONG_PCT = 80             # W.A: 85 → 80 (with min=10, still strong)
CONSENSUS_SHORT_PCT = 80            # W.A: 85 → 80
DIVERGENCE_LONG_PCT = 75            # smart_long_pct >= this AND rekt_short_pct >= this → DIVERGENCE_LONG
DIVERGENCE_SHORT_PCT = 75           # smart_short_pct >= this AND rekt_long_pct >= this → DIVERGENCE_SHORT
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
WHALE_SL_ATR_MULT = 2.5              # W.A: 1.5 → 2.5 (12/14 SL-hit failure mode; same fix that helped breakout)
WHALE_TP_ATR_MULT = 4.0              # W.A: 3.0 → 4.0 (preserve ~1.6R RR with wider SL)

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

# ─── Backtest stats (for the dashboard's Yearly Projection tab) ─────────────
# Source: backtest_whale_proxy.py against OKX 24-month perp futures data
# (Apr 2024 - Apr 2026) with tightened thresholds (funding pctile 95).
# Caveat: this is the SYNTHETIC proxy, not the real whale-basket signal.
# The real signal has additional filters (edge-decay guard, divergence,
# min-trader floor) the proxy lacks, so live performance is expected
# to match or exceed these numbers.
WHALE_BACKTEST_STATS = {
    "pf": 1.24,
    "trades": 324,                # over 24 months
    "pnl_pct": 7.81,              # Net $780.89 / $10k baseline = 7.81%
    "dd_pct": 2.30,               # Max DD $230 / $10k = 2.30%
    "win_rate": 40.4,
    "sharpe": 0.72,
    "years": 2.0,                 # window length (differs from momentum's 5.3yr)
    "name": "Whale Tracker (Hyperliquid Smart Money)",
    "source": "synthetic proxy — funding-extreme fade",
}
