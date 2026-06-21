"""Phase N — crossover bot configuration (1-hour dual-SMA crossover).

A 1:2 R/R fixed-bracket strategy on the 1h chart:
  - LONG  on the bar where SMA(close, 20) crosses ABOVE SMA(close, 50)
  - SHORT on the bar where SMA(close, 20) crosses BELOW SMA(close, 50)
  - Exits: SL -1%, TP +2%, per-asset 10-min re-entry cooldown

No filter stack — pure crossover trigger. The Phase N.2 sweep
established that a higher-TF trend filter on top of 1h base is a
no-op (the filter applies on the SAME timeframe), so the primitive
stands alone.

═══════════════════════════════════════════════════════════════════════
PHASE N.2 — PROMOTED FROM 15-VARIANT SWEEP (Jun 20 2026, post-Phase N.X)
═══════════════════════════════════════════════════════════════════════

Phase N.X retired the original 5m/SMA50-100/1%-2% baseline as no-edge
(218 trades, mean WR 31% vs 36% fee-adjusted breakeven). The Phase N.2
sweep (tools/sweep_crossover_variants.py) tested 15 systematic variants
across timeframe (5m/15m/1h/4h) × SMA pair (9/21, 20/50, 50/100,
50/200) × higher-TF trend filter (on/off) × R/R bracket (1%/2%, 1%/3%,
1.5%/3%) and identified C4 as the decisive winner:

  Variant C4: 1h base, SMA(20)/SMA(50), no filter, -1%/+2% bracket
  Window:    1195 1h bars (~50 days) per asset, Coinbase
  Aggregate: meanPF=1.73, medianPF=1.80, n=104, totRet=+46.1%, DD=7.7%
  Gate:      PASS (>= 4/6 assets passing PF>=1.3, max DD <= 25%)

Per-asset results from sweep (4 promoted, 2 candidates):
  ETH_1H   PF=2.94  n=16  WR=62.5%  total=+16.6%  DD=2.9%  → LIVE
  XRP_1H   PF=2.15  n=16  WR=50.0%  total=+11.4%  DD=4.5%  → LIVE
  LINK_1H  PF=1.92  n=21  WR=52.4%  total=+13.0%  DD=4.3%  → LIVE
  SOL_1H   PF=1.67  n=16  WR=50.0%  total= +7.9%  DD=4.9%  → LIVE
  DOGE_1H  PF=1.00  n=24  WR=37.5%  total= +0.1%  DD=7.7%  → candidate
  BTC_1H   PF=0.69  n=11  WR=27.3%  total= -2.9%  DD=7.1%  → candidate

Why 1h won. SMA(20)/SMA(50) on 1h = 20h / 50h ≈ 1d / 2d of trend
context — actual trend-following territory. The original 5m timeframe's
50/100 equivalent (~4h / 8h) was too short for the primitive's
strength. 1h also has ~12× fewer trades than 5m → ~12× less fee drag.

Why BTC failed. Looser conjecture: BTC's lower volatility on 1h
generates fewer high-conviction crosses; the few that fire are more
likely to be whipsaws. Worth re-testing in a future regime change.

Sizing: $10 margin × 10x = $100 notional. With 1h signals firing
~4 per asset per 50 days (vs scalp's ~3 per day), 3 concurrent
positions × $100 = $300 gross max is more than enough capacity. Per-trade
downside capped at $100 × 1% = $1.00 SL hit.

Day-14 review rubric (mirror Phase M):
  PF >= 1.3, n >= 30 across 4 live assets → expand (promote BTC/DOGE
                                              if their numbers improve)
  PF 1.0 - 1.3                            → hold, observe another 14 days
  PF <  1.0                               → retire to Phase N.X again
"""

from __future__ import annotations

import os
from pathlib import Path

_BOT_DIR = Path(__file__).resolve().parent

# ─── Master pause flag ─────────────────────────────────────────────────
# Defaults TRUE so deploying the code never opens a live position
# accidentally. Flip via `CROSSOVER_PAUSED=false` in .env once paper
# backtest clears the gates.
CROSSOVER_PAUSED = os.getenv("CROSSOVER_PAUSED", "true").lower() in ("true", "1", "yes")

# ─── Polling cadence ───────────────────────────────────────────────────
# Poll every minute; the strategy only acts on FRESHLY CLOSED 5m bars.
# Sub-minute responsiveness needed for SL/TP exit management on active
# positions (price can blow through both within seconds in fast moves).
CROSSOVER_POLL_INTERVAL_SECONDS = 60
# Phase N.2: moved 5m → 1h after the sweep. Bars close hourly so signal
# checking only matters once per hour; sub-minute polling is for
# SL/TP exit responsiveness (price can blow through both within seconds
# in fast moves), and we keep that cadence.
CROSSOVER_INTERVAL = "1h"

# ─── State + naming ────────────────────────────────────────────────────
CROSSOVER_STATE_KEY_PREFIX = "CROSSOVER_"
CROSSOVER_STRATEGY_TAG = "Crossover"
CROSSOVER_HEARTBEAT_FILE = _BOT_DIR / ".crossover_heartbeat"
CROSSOVER_SIGNAL_LOG     = _BOT_DIR / "crossover_signals.jsonl"

# ─── Position sizing + caps ────────────────────────────────────────────
# Same shape as scalp — both 5m bots, comparable risk profile.
CROSSOVER_MARGIN_PER_TRADE = 10.0
CROSSOVER_LEVERAGE = 10
# 3 concurrent positions × $100 notional = $300 gross max.
# Per-trade downside capped at $100 × 1% = $1.00 SL hit.
MAX_CROSSOVER_POSITIONS = 3

# Per-asset re-entry cooldown (seconds). Crossover-trigger semantics
# already prevent immediate re-entry (no fresh cross within minutes of
# an exit), but a 10-min cooldown is cheap insurance against rapid
# whipsaw bars producing a second cross right after a SL hit.
CROSSOVER_COOLDOWN_SECONDS = 600

# ─── Per-asset baseline (factory) ──────────────────────────────────────

def _crossover_default(symbol: str, name: str) -> dict:
    """Build the Phase N.2 baseline crossover config for one asset.

    Phase N.2 winning variant from the 15-config sweep:
      - 1h timeframe
      - SMA(20) / SMA(50) crossover
      - -1% / +2% bracket
      - No filter stack
      - SHORT enabled
    """
    return {
        "symbol":            symbol,
        "interval":          CROSSOVER_INTERVAL,    # "1h"
        "sma_fast":          20,                     # Phase N.2: was 50
        "sma_slow":          50,                     # Phase N.2: was 100
        "sl_pct":            1.0,
        "tp_pct":            2.0,
        "allow_short":       True,
        # L.2 regime gate OFF — Daily classifier hits don't match the
        # 1h signal cadence. Higher-TF trend filter also OFF — at 1h
        # base TF, the resample-to-1h is a no-op (Phase N.2 sweep
        # showed B2/E1 identical to A1/C4 for this reason).
        "use_regime_gate":   False,
        "use_btc_filter":    False,
        "strategy_name":     name,
    }


# ─── Live universe — Phase N.2 promoted set (Jun 20 2026) ────────────
# 50-day Coinbase 1h backtest from the variant sweep cleared PF>=1.3,
# n>=11, DD<=8% on 4 of 6 working assets. Promoted to live PAUSED
# pending validator confirmation + 14-day paper observation.
CROSSOVER_ASSETS = {
    "ETH_1H":  _crossover_default("ETHUSDT",  "ETH 1h Crossover"),
    "SOL_1H":  _crossover_default("SOLUSDT",  "SOL 1h Crossover"),
    "XRP_1H":  _crossover_default("XRPUSDT",  "XRP 1h Crossover"),
    "LINK_1H": _crossover_default("LINKUSDT", "LINK 1h Crossover"),
}

# ─── Candidate universe — sub-gate or data-limited assets ────────────
# BTC_1H:  PF=0.69, n=11 in sweep. Lower volatility on 1h produces
#          fewer high-conviction crosses. Re-test in a future regime.
# DOGE_1H: PF=1.00 exactly — net flat. Borderline; another 14 days of
#          paper might tip it positive or confirm flat.
# ADA_1H/AVAX_1H: data-short on Coinbase (~290 5m bars; the 1h depth
#          would be ~50 bars, insufficient for SMA50 + 2 + signal lookback).
# BNB/TRX: not on Coinbase Exchange at all.
CROSSOVER_CANDIDATE_ASSETS = {
    "BTC_1H":  _crossover_default("BTCUSDT",  "BTC 1h Crossover"),
    "DOGE_1H": _crossover_default("DOGEUSDT", "DOGE 1h Crossover"),
    "ADA_1H":  _crossover_default("ADAUSDT",  "ADA 1h Crossover"),
    "AVAX_1H": _crossover_default("AVAXUSDT", "AVAX 1h Crossover"),
}

# ─── Backtest stats for projection table (Phase N.2 promotion) ──────
# 1195-bar 1h Coinbase window = ~0.14yr (50 days). Years deliberately
# accurate so L.1 projection layer's per-row windowing math is right.
# trades count per-asset is small — projection display will mark as
# "low confidence" via Phase J.6's confidence-pill logic.
CROSSOVER_BACKTEST_STATS = {
    "ETH_1H":  {"pf": 2.94, "trades": 16, "pnl_pct":  16.6, "dd_pct": 2.9,
                 "wr": 62.5, "years": 0.14,
                 "source": "1195-bar 1h Coinbase sweep (N.2 variant C4)"},
    "SOL_1H":  {"pf": 1.67, "trades": 16, "pnl_pct":   7.9, "dd_pct": 4.9,
                 "wr": 50.0, "years": 0.14,
                 "source": "1195-bar 1h Coinbase sweep (N.2 variant C4)"},
    "XRP_1H":  {"pf": 2.15, "trades": 16, "pnl_pct":  11.4, "dd_pct": 4.5,
                 "wr": 50.0, "years": 0.14,
                 "source": "1195-bar 1h Coinbase sweep (N.2 variant C4)"},
    "LINK_1H": {"pf": 1.92, "trades": 21, "pnl_pct":  13.0, "dd_pct": 4.3,
                 "wr": 52.4, "years": 0.14,
                 "source": "1195-bar 1h Coinbase sweep (N.2 variant C4)"},
}
