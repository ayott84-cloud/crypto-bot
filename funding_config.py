"""Funding-fade bot configuration.

All knobs specific to the funding-rate-fade strategy live here. Both
peer reviewers' consensus is encoded in the defaults:

  - 97th-percentile threshold + 0.05% absolute floor (ChatGPT & Gemini agreed
    95th was too noisy; 99th too rare. 97 is the middle path with abs-floor
    to clear costs on small-notional WEEX trades.)
  - Execute only near funding settlement (T-30 to T+30 around 8h fixings).
    Outside the window, high funding can persist for days without reverting.
  - ATR regime FLIPPED relative to the whale bot — fade-style strategies
    want LOW realized vol (mean-reversion happens in calm tape).
  - Asymmetric exits: SL 2.5×ATR, TP 1.5×ATR. Mean reversion is fast or
    doesn't happen; don't try to capture a full reversal.
"""

from __future__ import annotations

import os
from pathlib import Path

_BOT_DIR = Path(__file__).resolve().parent

# ─── Master pause flag ───────────────────────────────────────────────────────
# Set FUNDING_PAUSED=true in .env to stop new entries; existing positions
# still manage to exit. Default false so the bot starts trading on deploy.
FUNDING_PAUSED = os.getenv("FUNDING_PAUSED", "false").lower() in ("true", "1", "yes")

# ─── Polling cadence ─────────────────────────────────────────────────────────
# Poll hourly. Inside the 60-minute window around each 8h funding fixing
# (00:00, 08:00, 16:00 UTC), we actually consider trades. Outside that
# window we just refresh the local rolling history.
FUNDING_POLL_INTERVAL_SECONDS = 60 * 60  # 1 hour

# Window around funding settlement when entries are allowed (peer-review:
# "execute near funding-fixing time"). T-30min through T+30min.
FUNDING_EXECUTION_WINDOW_MINUTES = 30

# Funding fixings on Binance/HL/most majors happen every 8h at 00, 08, 16 UTC.
# WEEX matches this convention.
FUNDING_FIXING_HOURS_UTC = (0, 8, 16)

# ─── Signal thresholds ───────────────────────────────────────────────────────
# Percentile of current rate within the rolling 30-day distribution.
# Per peer-review: 95 too noisy, 99 too rare. 97 is the consensus.
FUNDING_PERCENTILE_THRESHOLD = 97.0

# Absolute minimum funding magnitude (per-8h rate) to consider extreme.
# 0.0005 = 0.05% per 8h ≈ 55% APR. Below this, fees + slippage eat the trade.
FUNDING_ABSOLUTE_FLOOR = 0.0005

# Minimum number of data points in the rolling history before classifying.
# At 3 funding fixings per day × 30 days = 90 ideal. We accept anything
# above 45 (15 days) so the bot can start trading partway through warmup.
FUNDING_MIN_HISTORY_POINTS = 45

# ─── Filters (gate trades after a signal classifies) ─────────────────────────

# Minimum 24h open interest in USD on WEEX for the symbol. Avoids illiquid
# books where funding is just noise.
FUNDING_MIN_OI_USD = 20_000_000

# ATR regime — FLIPPED vs whale bot. Mean-reversion fades want low realized
# vol. Trade only when current 4H ATR is below its 20-period SMA.
FUNDING_REQUIRE_LOW_VOL = True
FUNDING_ATR_PERIOD = 14
FUNDING_ATR_INTERVAL = "4h"
FUNDING_ATR_SMA_PERIOD = 20

# Trend-against-fade guard. For a SHORT fade (positive funding crowded),
# don't enter if price is strongly trending up (above EMA20 with positive
# slope). Same logic mirrored for LONG fade. This filter is what kept the
# whale bot from blowing up on Trade #10 conceptually — and the peer
# reviewers were unanimous it should be on every directional fade.
FUNDING_USE_TREND_FILTER = True
FUNDING_TREND_EMA_PERIOD = 20

# Per-coin cooldown after a position closes (avoid re-fading the same coin
# repeatedly during one funding extreme).
FUNDING_COOLDOWN_HOURS = 8  # next funding cycle

# ─── Sizing ──────────────────────────────────────────────────────────────────
# Start half-size while validating. After ≥10 closed trades with positive
# expectancy, can lift to FUNDING_MARGIN_FULL.
FUNDING_MARGIN_USD = 25.0      # validation sizing → $250 notional
FUNDING_MARGIN_FULL = 50.0     # post-validation lift target ($500 notional)
FUNDING_LEVERAGE = 10

# ─── Exits ───────────────────────────────────────────────────────────────────
# Asymmetric per ChatGPT: mean reversion is fast or fails. Don't try to
# capture full reversal — exit quickly.
FUNDING_SL_ATR_MULT = 2.5
FUNDING_TP_ATR_MULT = 1.5

# Time-stop: close at the next funding fixing (8h max hold). If reversion
# was going to happen, it happens within one cycle.
FUNDING_TIME_STOP_HOURS = 8

# Funding-normalize exit: if the funding rate moves back inside the inner
# 50% of its 30-day distribution (between 25th and 75th percentile), we
# consider the thesis played out and exit at market.
FUNDING_NORMALIZE_TO_INNER_BAND = True
FUNDING_NORMALIZE_INNER_LOW_PCTILE = 25
FUNDING_NORMALIZE_INNER_HIGH_PCTILE = 75

# ─── Symbol universe ─────────────────────────────────────────────────────────
# Intersect WEEX-listed perps with top-100 market cap (same plumbing whale
# bot uses). Skip the funding-extreme signal on coins outside top-100 —
# they have noisy funding and shallow books.
# The whale_universe module is reused; nothing new to configure here.

# ─── State / logging paths ───────────────────────────────────────────────────
FUNDING_STATE_KEY_PREFIX = "FUNDING_"  # state.json keys, e.g. "FUNDING_BTC"
FUNDING_STRATEGY_TAG = "Funding Fade"  # journal prefix, e.g. "Funding Fade BTC SHORT"

FUNDING_SIGNAL_LOG = _BOT_DIR / "funding_signals.jsonl"  # append per-cycle
FUNDING_HEARTBEAT = _BOT_DIR / ".funding_heartbeat"
