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
# Phase M.3 (Jul 2026): moved 5m → 15m. Cost-floor research (arXiv MNQ
# study + crypto TA cost-adjustment papers) says 5m tight-bracket OHLCV
# strategies rarely clear round-trip fees; 15m keeps the vol-expansion
# signal while the ATR bracket gets room to breathe outside wick noise.
SCALP_INTERVAL = "15m"

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
    """Build the baseline 5m scalp config for one asset.

    Phase M.2: this baseline now turns on 4 enhancement filters that
    the M.1 backtest showed are needed for the strategy to have edge.
    Per-asset overrides can still disable any of them. Defaults:
      - vol_expansion_threshold = 1.5  (tightened from 1.0)
      - use_volume_filter        = True (1.5× SMA(volume, 20))
      - use_higher_tf_trend      = True (1h EMA20 vs EMA50 alignment)
      - use_rsi_extreme_filter   = True (block LONG > 70 / SHORT < 30)
    """
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
        # ── Phase M.2 enhancement filters (defaults ON) ──
        "vol_expansion_threshold": 1.5,
        "use_volume_filter":       True,
        "vol_threshold_mult":      1.5,
        "vol_sma_period":          20,
        "use_higher_tf_trend":     True,
        "higher_tf_interval":      "1h",
        "higher_tf_ema_fast":      20,
        "higher_tf_ema_slow":      50,
        "use_rsi_extreme_filter":  True,
        "rsi_period":              14,
        "rsi_overbought":          70.0,
        "rsi_oversold":            30.0,
        # ── Phase M.3 (Jul 2026 research redesign) ──
        # ATR-scaled bracket: SL = 2.5 x ATR(14) (outside wick noise),
        # TP = 1.5R (practitioner sweet spot; 2:1 needs 42%+ WR).
        "use_atr_bracket":         True,
        "atr_period":              14,
        "atr_sl_mult":             2.5,
        "tp_r_multiple":           1.5,
        # Fixed-dollar risk sizing: every stopped trade loses the same $.
        # Notional still capped at margin x leverage.
        "risk_usd_per_trade":      2.0,
        # Triple-barrier time limit: 16 x 15m = 4h. A trade that hasn't
        # resolved by then is no longer the trade we entered.
        "time_limit_bars":         16,
        # P2.3 daily 9-MA regime gate
        "use_daily_regime":        True,
        "strategy_name":      name,
    }


# ─── Live universe — P4 Step-2 survivors (Jul 4 2026) ─────────────────
# 35,000-bar (≈1 year) 15m Coinbase HONEST replay — conservative
# intra-bar fills, 0.15% round-trip costs, deployed M.3 exits:
#   BTC_5M  PF=1.18  n=45  WR=51.1%  total= +3.1%  DD= 6.2%
#   ETH_5M  PF=1.54  n=28  WR=50.0%  total= +8.8%  DD= 7.3%  ← lead
#   XRP_5M  PF=1.25  n=25  WR=52.0%  total= +4.9%  DD= 7.4%
# ETH clears the Step-2 gate outright (PF≥1.3, DD≤15%, avg win ~1.8%);
# BTC/XRP are positive-but-sub-gate and stay live-paper as observation.
# M.3 note: dict KEYS keep the legacy _5M suffix (they're position-state
# keys — renaming would orphan journal/state history); display names and
# the interval now say 15m.
SCALP_ASSETS = {
    "BTC_5M":  _scalp_default("BTCUSDT",  "BTC 15m Scalp"),
    "ETH_5M":  _scalp_default("ETHUSDT",  "ETH 15m Scalp"),
    "XRP_5M":  _scalp_default("XRPUSDT",  "XRP 15m Scalp"),
}

# ─── Candidate universe — failed gates or no data source ──────────────
# DOGE_5M, LINK_5M: DEMOTED at the P4 Step-2 review (Jul 4 2026) — the
# 1-year honest replay ruled them out (DOGE PF=0.41 DD=24.7%; LINK
# PF=0.76). The 17-day M.2 numbers that promoted them (PF inf / 7.98)
# were liberal-fill artifacts.
#
# SOL_5M: M.2 backtest PF=0.47. Regime-dependent; revisit.
# ADA_5M, AVAX_5M: Coinbase data gap in the M.2 window (n=0).
# BNB_5M, TRX_5M: NOT listed on Coinbase — no honest long-window source.
SCALP_CANDIDATE_ASSETS = {
    "DOGE_5M": _scalp_default("DOGEUSDT", "DOGE 15m Scalp"),
    "LINK_5M": _scalp_default("LINKUSDT", "LINK 15m Scalp"),
    "SOL_5M":  _scalp_default("SOLUSDT",  "SOL 15m Scalp"),
    "ADA_5M":  _scalp_default("ADAUSDT",  "ADA 15m Scalp"),
    "AVAX_5M": _scalp_default("AVAXUSDT", "AVAX 15m Scalp"),
    "BNB_5M":  _scalp_default("BNBUSDT",  "BNB 15m Scalp"),
    "TRX_5M":  _scalp_default("TRXUSDT",  "TRX 15m Scalp"),
}

# ─── Backtest stats for projection table (P4 Step-2 honest numbers) ──
# 35,000-bar 15m Coinbase replay ≈ 1.0yr. These REPLACE the M.2
# liberal-fill stats (PF 6.02/999/7.98) that inflated the projection —
# the honest pipeline (P2.1 intra-bar fills + P2.2 costs + M.3 exits)
# is the only source the projection may cite.
_SCALP_STATS_SOURCE = "35000-bar 15m Coinbase honest replay (M.3 exits, costs)"
SCALP_BACKTEST_STATS = {
    "BTC_5M":  {"pf": 1.18, "trades": 45, "pnl_pct":  3.1, "dd_pct":  6.2,
                 "wr": 51.1, "years": 1.0, "source": _SCALP_STATS_SOURCE},
    "ETH_5M":  {"pf": 1.54, "trades": 28, "pnl_pct":  8.8, "dd_pct":  7.3,
                 "wr": 50.0, "years": 1.0, "source": _SCALP_STATS_SOURCE},
    "XRP_5M":  {"pf": 1.25, "trades": 25, "pnl_pct":  4.9, "dd_pct":  7.4,
                 "wr": 52.0, "years": 1.0, "source": _SCALP_STATS_SOURCE},
    "DOGE_5M": {"pf": 0.41, "trades": 32, "pnl_pct": -20.8, "dd_pct": 24.7,
                 "wr": 31.2, "years": 1.0,
                 "source": _SCALP_STATS_SOURCE + " — DEMOTED"},
    "LINK_5M": {"pf": 0.76, "trades": 17, "pnl_pct":  -3.0, "dd_pct":  5.1,
                 "wr": 52.9, "years": 1.0,
                 "source": _SCALP_STATS_SOURCE + " — DEMOTED"},
}
