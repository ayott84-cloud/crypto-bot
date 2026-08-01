"""Module 2 — stock sleeve configuration.

Mirrors the crypto per-bot config convention: a paused-by-default env
flag, state-key prefix, strategy tag, heartbeat path, sizing, and the
asset universe. New bots default to PAUSED until validated — Module 2
is no exception.

THREE SLEEVES, ONE DAEMON. All three decide on daily bars at or near
the close, so they share a process (and therefore one position_manager
owner, "stock"). Per-sleeve granularity lives on the journal /
kill_switch axis via the strategy tag, so each sleeve still gets its own
loss streak, gate step and fleet_review row.

STRATEGY NAMES MUST END WITH THE SLEEVE TAG — journal._bot_tag and
kill_switch._bot_of both classify by suffix, and the default sink is
Momentum. A misnamed stock strategy would silently pollute crypto stats.

UNIVERSE NOTE: backtest length is bounded by the youngest ETF in a
sleeve, not by data availability. GSG lists 2006 and VEU/BIL 2007, so
the trend sleeve reaches ~20 years and dual ~19 — both comfortably
spanning 2008, 2020 and 2022, which are the regimes that matter for
strategies whose entire job is drawdown compression.
"""

from __future__ import annotations

import os
from pathlib import Path

_BOT_DIR = Path(__file__).resolve().parent

# ─── Daemon-level ─────────────────────────────────────────────────────────

STOCK_PAUSED = os.getenv("STOCK_PAUSED", "true").lower() in ("true", "1", "yes")
STOCK_POLL_INTERVAL_SECONDS = 300
STOCK_STATE_KEY_PREFIX = "STOCK_"
STOCK_HEARTBEAT_FILE = _BOT_DIR / ".stock_heartbeat"
STOCK_SIGNAL_LOG = _BOT_DIR / "stock_signals.jsonl"

# Cash account, no leverage. Equities are not perps; 1x is the honest
# default and sidesteps Reg T maintenance entirely.
STOCK_MARGIN_PER_TRADE = 100.0
STOCK_LEVERAGE = 1
MAX_STOCK_POSITIONS = 6

# ─── Sleeve tags (suffix-classified — see module docstring) ──────────────

TREND_STRATEGY_TAG = "StockTrend"
DUAL_STRATEGY_TAG = "StockDual"
REV_STRATEGY_TAG = "StockRev"

# ─── S1: trend regime (Faber QTAA) ────────────────────────────────────────
# Each sleeve member is in-or-out on its OWN signal, equal weight.
# 200 trading days ~= the canonical 10-month SMA.

_TREND_DEFAULTS = {
    "sma_period": 200,
    "interval": "1d",
    "rebalance": "month_end",
    "asset_class": "equity_etf",
}

STOCK_TREND_ASSETS = {
    f"{sym}_TREND": {**_TREND_DEFAULTS, "symbol": sym,
                      "strategy_name": f"{sym} 1M {TREND_STRATEGY_TAG}"}
    for sym in ("SPY", "EFA", "AGG", "VNQ", "GSG")
}

# ─── S2: dual momentum (Antonacci GEM), ensemble ─────────────────────────
# Lookback ensemble, never a single spec: ReSolve tested 1,226 variants
# and the canonical 12/1 beat only 61% of its siblings. The cash leg is
# mandatory — 2022 had stocks AND bonds falling together.

STOCK_DUAL_CONFIG = {
    "risk_assets": ["SPY", "VEU"],
    "safe_asset": "AGG",
    "cash_asset": "BIL",
    "lookbacks_months": [3, 6, 9, 12],
    "interval": "1d",
    "rebalance": "month_end",
    "asset_class": "equity_etf",
    "strategy_name": f"GEM 1M {DUAL_STRATEGY_TAG}",
}

# ─── S3: short-term reversion (IBS / RSI-2) ──────────────────────────────
# INDEX ETFs ONLY. The published decay concentrates in single names where
# costs eat the edge; on SPY-class liquidity the round trip is ~5bps.

_REV_DEFAULTS = {
    "sma_period": 200,
    "ibs_threshold": 0.2,
    "rsi_period": 2,
    "rsi_threshold": 10.0,
    "exit_sma_period": 5,
    "rsi_exit": 70.0,
    "max_hold_bars": 10,
    "interval": "1d",
    "asset_class": "equity_etf",
}

STOCK_REV_ASSETS = {
    f"{sym}_REV": {**_REV_DEFAULTS, "symbol": sym,
                    "strategy_name": f"{sym} 1D {REV_STRATEGY_TAG}"}
    for sym in ("SPY", "QQQ", "IWM", "EFA")
}

# Candidates — not traded, kept so a demoted asset stays exit-manageable
# and so the dashboard can show what was considered and rejected.
STOCK_TREND_CANDIDATE_ASSETS: dict = {}
STOCK_REV_CANDIDATE_ASSETS: dict = {}

# Populated after the S2 validation runs, same shape as the crypto
# *_BACKTEST_STATS dicts (pf/trades/pnl_pct/dd_pct/wr/years/source).
STOCK_BACKTEST_STATS: dict = {}


def all_symbols() -> list:
    """Every symbol any sleeve needs — the data layer's fetch list."""
    syms = {c["symbol"] for c in STOCK_TREND_ASSETS.values()}
    syms |= {c["symbol"] for c in STOCK_REV_ASSETS.values()}
    syms |= set(STOCK_DUAL_CONFIG["risk_assets"])
    syms |= {STOCK_DUAL_CONFIG["safe_asset"], STOCK_DUAL_CONFIG["cash_asset"]}
    return sorted(syms)
