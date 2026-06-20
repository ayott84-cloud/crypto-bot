"""Phase N — crossover bot configuration (5-minute dual-SMA crossover).

A 1:2 R/R fixed-bracket strategy on the 5m chart with the simplest
possible signal:
  - LONG  on the bar where SMA(close, 50) crosses ABOVE SMA(close, 100)
  - SHORT on the bar where SMA(close, 50) crosses BELOW SMA(close, 100)
  - Exits: SL -1%, TP +2%, per-asset 10-min re-entry cooldown

No filter stack by default. The whole point of Phase N was a clean
baseline test of the canonical dual-MA-crossover strategy.

═══════════════════════════════════════════════════════════════════════
PHASE N.X — PERMANENTLY DEFERRED (Jun 20 2026, post-validator)
═══════════════════════════════════════════════════════════════════════

Final disposition. The 5000-bar Coinbase validator over the 8-asset
universe confirmed the strategy has no edge on 5m crypto:

  BTC_5M   bars=5000  PF=1.13  n=26  WR=38.5%  total= +2.6%  DD= 8.5%  fail
  ETH_5M   bars=5000  PF=0.49  n=37  WR=21.6%  total=-18.4%  DD=24.0%  LOSING
  SOL_5M   bars=5000  PF=1.08  n=40  WR=40.0%  total= +2.7%  DD= 8.7%  fail
  XRP_5M   bars=5000  PF=0.78  n=37  WR=29.7%  total= -6.9%  DD=13.5%  fail
  DOGE_5M  bars=5000  PF=0.93  n=40  WR=35.0%  total= -2.4%  DD= 9.9%  fail
  LINK_5M  bars=5000  PF=0.47  n=38  WR=21.1%  total=-20.4%  DD=20.4%  LOSING
  ADA_5M   bars= 298  (Coinbase historical depth insufficient)
  AVAX_5M  bars= 291  (Coinbase historical depth insufficient)

Aggregate over 218 trades, 6 assets:
  - Mean WR ≈ 31% vs fee-adjusted breakeven of 36% (0.04% WEEX taker × 2 sides)
  - Wilson 95% CI [25%, 37%] — overwhelmingly BELOW breakeven
  - 4 of 6 assets net losing; best PF is 1.13 (BTC, fee drag wipes it)
  - No asset clears the PF≥1.5 / DD≤8% gate

This matches the published-literature band exactly: raw dual-MA-crossover
on 5m crypto sits at PF 0.8-1.4. The 1%/2% bracket plus WEEX's 0.08%
round-trip fee eats whatever marginal edge exists, and shorter timeframes
amplify whipsaw losses in choppy regimes.

Operator decision: retire the strategy. Mirrors Reversal Phase I.X.
  - CROSSOVER_PAUSED stays True indefinitely
  - crypto-crossover.service stays uninstalled on the droplet
  - Code stays in repo (crossover_signals.py, crossover_main.py, this
    file, templates/tabs/crossover.html.j2) for institutional memory
  - Dashboard's Crossover tab will show "DORMANT — strategy deferred
    (no edge in 218-trade Coinbase backtest)" once why-silent copy is
    updated
  - No further tuning planned — the result is statistically decisive

Future re-attempts (NOT scheduled). Most promising directions if ever
revisited:
  1. Different timeframe — 1h or 4h where SMA50/SMA100 captures
     genuine trend rather than 5m noise. (4.2h / 8.3h equivalents on
     5m are too short for the primitive's strength.)
  2. Different SMA pair — 9/21 (faster, more crosses) or 50/200
     (slower, fewer but higher-conviction crosses).
  3. Wider R/R — 1%/3% or 1.5%/3% to absorb fee drag.
  4. Single-filter addition — most promising is a 1h trend gate
     (LONG only when 1h EMA20 > EMA50). Backtested separately, that
     might lift PF to 1.5+ on the assets at 0.9-1.1.

Universe original spec (kept for reference + potential future reuse):
top-10 minus BNB/TRX (not on Coinbase Exchange) minus stablecoins.
Same 8-asset universe as Phase M.1's initial sweep so results are
directly comparable across strategies.
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
