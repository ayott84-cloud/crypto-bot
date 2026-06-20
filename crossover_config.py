"""Phase N — crossover bot configuration (5-minute dual-SMA crossover).

A 1:2 R/R fixed-bracket strategy on the 5m chart with the simplest
possible signal:
  - LONG  on the bar where SMA(close, 50) crosses ABOVE SMA(close, 100)
  - SHORT on the bar where SMA(close, 50) crosses BELOW SMA(close, 100)
  - Exits: SL -1%, TP +2%, per-asset 10-min re-entry cooldown

No filter stack by default. The whole point of Phase N is a clean
baseline test of the canonical dual-MA-crossover strategy. If the
unfiltered backtest shows edge, no filters are needed; if not, we
know the primitive lacks edge for 5m crypto in this regime.

Universe: top-10 minus BNB/TRX (not on Coinbase, can't backtest).
Initial backtest: validate all 8 traded assets via
tools/validate_crossover_candidates.py.

Sizing: $10 margin × 10x = $100 notional, matching scalp's profile.
Scalp + crossover are both 5m bots with similar trade frequency
expectations — keeping margin uniform makes per-bot Sortino comparable.
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
CROSSOVER_INTERVAL = "5m"

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
    """Build the baseline 5m crossover config for one asset.

    Phase N starting point: no filter stack. Pure SMA50/SMA100 crossover
    with -1%/+2% bracket. If a baseline backtest shows lack of edge per
    asset, individual filters can be enabled via per-asset overrides:
      - use_higher_tf_trend / higher_tf_interval / higher_tf_ema_*
      - use_volume_filter / vol_threshold_mult / vol_sma_period
      - use_rsi_extreme_filter / rsi_period / rsi_overbought / rsi_oversold
    The analyzer ignores those flags by default — wiring them in is a
    Phase N.x enhancement if the baseline disappoints.
    """
    return {
        "symbol":            symbol,
        "interval":          CROSSOVER_INTERVAL,
        "sma_fast":          50,
        "sma_slow":          100,
        "sl_pct":            1.0,
        "tp_pct":            2.0,
        "allow_short":       True,
        # L.2 regime gate intentionally OFF — Daily classifier is far
        # too slow for 5m signals. Same rationale as scalp_config.
        "use_regime_gate":   False,
        "use_btc_filter":    False,
        "strategy_name":     name,
    }


# ─── Live universe — empty until backtest validates ──────────────────
# Phase N starts with an empty live set. Operator runs the validator,
# reads results, promotes assets that pass gates into CROSSOVER_ASSETS.
CROSSOVER_ASSETS: dict = {}

# ─── Candidate universe — backtest pool ──────────────────────────────
# Top-10 minus stablecoins minus BNB/TRX (not on Coinbase Exchange).
# Same 8-asset universe as Phase M.1's initial sweep so results are
# directly comparable across strategies.
CROSSOVER_CANDIDATE_ASSETS = {
    "BTC_5M":  _crossover_default("BTCUSDT",  "BTC 5m Crossover"),
    "ETH_5M":  _crossover_default("ETHUSDT",  "ETH 5m Crossover"),
    "SOL_5M":  _crossover_default("SOLUSDT",  "SOL 5m Crossover"),
    "XRP_5M":  _crossover_default("XRPUSDT",  "XRP 5m Crossover"),
    "ADA_5M":  _crossover_default("ADAUSDT",  "ADA 5m Crossover"),
    "DOGE_5M": _crossover_default("DOGEUSDT", "DOGE 5m Crossover"),
    "AVAX_5M": _crossover_default("AVAXUSDT", "AVAX 5m Crossover"),
    "LINK_5M": _crossover_default("LINKUSDT", "LINK 5m Crossover"),
}

# ─── Backtest stats for projection table (populated post-validator) ─
# Empty until tools/validate_crossover_candidates.py runs and the
# operator promotes winners. Validator prints copy-paste blocks.
CROSSOVER_BACKTEST_STATS: dict = {}
