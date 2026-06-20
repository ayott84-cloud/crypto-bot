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
        "strategy_name":      name,
    }


# ─── Live universe — Phase M.2 promoted set (Jun 20 2026) ─────────────
# 17-day Coinbase backtest with M.2 4-filter stack:
#   BTC_5M   PF=6.02  n=4  WR=75%   total=+7.6%  DD=1.5%
#   ETH_5M   PF=2.75  n=5  WR=60%   total=+5.9%  DD=1.8%
#   XRP_5M   PF=1.83  n=4  WR=50%   total=+2.8%  DD=3.3%
#   DOGE_5M  PF=inf   n=4  WR=100%  total=+13.1% DD=0.0%
#   LINK_5M  PF=7.98  n=5  WR=80%   total=+10.9% DD=1.6%
# 5 of 6 traded assets positive at PF≥1.5 with DD≤8%. SOL_5M failed
# M.2 (PF=0.47, WR=25%) and stays as a candidate for revisit.
SCALP_ASSETS = {
    "BTC_5M":  _scalp_default("BTCUSDT",  "BTC 5m Scalp"),
    "ETH_5M":  _scalp_default("ETHUSDT",  "ETH 5m Scalp"),
    "XRP_5M":  _scalp_default("XRPUSDT",  "XRP 5m Scalp"),
    "DOGE_5M": _scalp_default("DOGEUSDT", "DOGE 5m Scalp"),
    "LINK_5M": _scalp_default("LINKUSDT", "LINK 5m Scalp"),
}

# ─── Candidate universe — failed or unavailable ───────────────────────
# SOL_5M: M.2 backtest PF=0.47 (worse than M.1's 1.04). The 4 filters
# that helped BTC/ETH/XRP/DOGE/LINK degraded SOL's edge. Regime-dependent.
# Revisit when SOL volatility profile changes.
#
# ADA_5M, AVAX_5M: Coinbase data gap in the M.2 window (n=0). May be
# liquidity issue or symbol-mapping. Validator skips them; safe to keep
# as candidates and re-check when M.2 paper accumulates more data.
#
# BNB_5M, TRX_5M: NOT listed on Coinbase Exchange (US-licensed venue).
# No historical 5m kline data accessible. Would require switching to a
# different backtest source (paid data provider). Skipped for now.
SCALP_CANDIDATE_ASSETS = {
    "SOL_5M":  _scalp_default("SOLUSDT",  "SOL 5m Scalp"),
    "ADA_5M":  _scalp_default("ADAUSDT",  "ADA 5m Scalp"),
    "AVAX_5M": _scalp_default("AVAXUSDT", "AVAX 5m Scalp"),
    "BNB_5M":  _scalp_default("BNBUSDT",  "BNB 5m Scalp"),
    "TRX_5M":  _scalp_default("TRXUSDT",  "TRX 5m Scalp"),
}

# ─── Backtest stats for projection table (Phase M.2 promotion stats) ─
# 5000-bar Coinbase replay window = 0.05yr (~17 days). Years deliberately
# accurate so the L.1 projection layer's per-row windowing math is right.
# trades count is tiny per asset — projection display will mark as "low
# confidence" via Phase J.6's confidence-pill logic.
SCALP_BACKTEST_STATS = {
    "BTC_5M":  {"pf": 6.02, "trades": 4, "pnl_pct":  7.6, "dd_pct": 1.5,
                 "wr": 75.0,  "years": 0.05,
                 "source": "5000-bar 5m Coinbase replay (M.2 filters)"},
    "ETH_5M":  {"pf": 2.75, "trades": 5, "pnl_pct":  5.9, "dd_pct": 1.8,
                 "wr": 60.0,  "years": 0.05,
                 "source": "5000-bar 5m Coinbase replay (M.2 filters)"},
    "XRP_5M":  {"pf": 1.83, "trades": 4, "pnl_pct":  2.8, "dd_pct": 3.3,
                 "wr": 50.0,  "years": 0.05,
                 "source": "5000-bar 5m Coinbase replay (M.2 filters)"},
    "DOGE_5M": {"pf": 999.0, "trades": 4, "pnl_pct": 13.1, "dd_pct": 0.0,
                 "wr": 100.0, "years": 0.05,
                 "source": "5000-bar 5m Coinbase replay (M.2 filters)"},
    "LINK_5M": {"pf": 7.98, "trades": 5, "pnl_pct": 10.9, "dd_pct": 1.6,
                 "wr": 80.0,  "years": 0.05,
                 "source": "5000-bar 5m Coinbase replay (M.2 filters)"},
}
