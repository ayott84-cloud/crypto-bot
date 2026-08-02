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
#
# SIZING MUST CLEAR ONE SHARE. Caught by a test: at $100/trade the
# daemon could not buy a single share of SPY (~$500-650) and silently
# skipped every entry — a strategy that never trades looks identical to
# one with no signals. $2,000 buys 3 SPY, and 6 concurrent positions
# deploys $12k against Alpaca's default $100k paper balance. Whole
# shares only (see StockExecutor.get_qty_step) because fractional
# positions complicate venue reconciliation for no benefit at this size.
STOCK_MARGIN_PER_TRADE = 2000.0
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

# Aug 1 2026 S2 cut: only SPY and QQQ cleared the gates. IWM failed on
# drawdown (25.7% vs a 20% bar) and DSR; EFA had no edge at all
# (PF 1.02 over 25yr, and 0.85 post-2013). Both are demoted to
# candidates rather than deleted — the operator's standing rule.
STOCK_REV_ASSETS = {
    f"{sym}_REV": {**_REV_DEFAULTS, "symbol": sym,
                    "strategy_name": f"{sym} 1D {REV_STRATEGY_TAG}"}
    for sym in ("SPY", "QQQ")
}

# Candidates — not traded, kept so a demoted asset stays exit-manageable
# and so the dashboard can show what was considered and rejected.
STOCK_REV_CANDIDATE_ASSETS = {
    f"{sym}_REV": {**_REV_DEFAULTS, "symbol": sym,
                    "strategy_name": f"{sym} 1D {REV_STRATEGY_TAG}"}
    for sym in ("IWM", "EFA")
}

# The whole trend sleeve failed S2. It stays defined (STOCK_TREND_ASSETS
# above) so the replay/validator can re-run it, but it is NOT in
# S3_APPROVED_SLEEVES and the daemon will not trade it.
STOCK_TREND_CANDIDATE_ASSETS: dict = dict(STOCK_TREND_ASSETS)

# S2 validation results (Aug 1 2026), same shape as the crypto
# *_BACKTEST_STATS dicts. Gates were registered in code BEFORE the data
# existed; three sleeves passed.
#
# READ THE BENCHMARK COLUMN. Every sleeve is buried by buy-and-hold on
# ABSOLUTE return (QQQ made +1694% while QQQ_REV made +173%). Their case
# is risk-adjusted: across the full 25 years — including the dot-com
# crash and 2008, where SPY drew down 55% — CAGR-per-drawdown favors the
# sleeves (SPY_REV 0.27 vs SPY 0.17). Over the 2013+ bull window alone
# buy-and-hold wins. Both are true, and anyone reading these numbers as
# "beats the market" has misread them.
_S2_SOURCE = "Aug 1 2026 honest replay, Tiingo adjusted daily, 5bps ETF cost"

STOCK_BACKTEST_STATS = {
    # ── PASSED all pre-registered gates ──
    "QQQ_REV":  {"pf": 1.64, "trades": 562, "pnl_pct": 173.1, "dd_pct": 17.2,
                  "wr": 0.0, "years": 25.0, "dsr": 0.996, "pbo": 0.17,
                  "bh_pnl_pct": 1694.2, "bh_dd_pct": 56.0,
                  "source": _S2_SOURCE + " — PASS"},
    "SPY_REV":  {"pf": 1.59, "trades": 562, "pnl_pct": 123.9, "dd_pct": 12.2,
                  "wr": 0.0, "years": 25.0, "dsr": 0.993, "pbo": 0.27,
                  "bh_pnl_pct": 845.8, "bh_dd_pct": 55.2,
                  "source": _S2_SOURCE + " — PASS"},
    "GEM":      {"pf": 0.0, "trades": 57, "pnl_pct": 160.8, "dd_pct": 17.7,
                  "wr": 0.0, "years": 19.1, "sharpe": 0.60, "dsr": 0.987,
                  "pbo": 0.26, "bh_pnl_pct": 845.8, "bh_dd_pct": 55.2,
                  "source": _S2_SOURCE + " — PASS (Sharpe-gated, not PF)"},
    # ── FAILED (kept for candidate-table honesty) ──
    "SPY_TREND": {"pf": 0.0, "trades": 76, "pnl_pct": 214.7, "dd_pct": 18.7,
                   "wr": 0.0, "years": 25.0, "sharpe": 0.48, "dsr": 1.000,
                   "pbo": 0.74, "bh_pnl_pct": 845.8, "bh_dd_pct": 55.2,
                   "source": _S2_SOURCE + " — FAIL: Sharpe 0.48<0.50, PBO 0.74"},
    "IWM_REV":  {"pf": 1.33, "trades": 496, "pnl_pct": 97.1, "dd_pct": 25.7,
                  "wr": 0.0, "years": 25.0, "dsr": 0.924, "pbo": 0.26,
                  "source": _S2_SOURCE + " — FAIL: DD 25.7%>20%, DSR 0.92"},
    "EFA_REV":  {"pf": 1.02, "trades": 521, "pnl_pct": 6.3, "dd_pct": 48.2,
                  "wr": 0.0, "years": 24.9, "dsr": 0.370, "pbo": 0.21,
                  "source": _S2_SOURCE + " — FAIL: no edge"},
    "GSG_TREND": {"pf": 0.0, "trades": 88, "pnl_pct": 11.1, "dd_pct": 75.1,
                   "wr": 0.0, "years": 20.0, "sharpe": 0.03, "dsr": 0.483,
                   "source": _S2_SOURCE + " — FAIL: Sharpe 0.03, DD 75%"},
}

# The sleeves cleared for Phase S3 paper trading. The trend sleeve is
# NOT here: it failed on Sharpe and its PBO of 0.74 (vs a 0.60 random
# baseline) says selecting an SMA period actively hurts out of sample —
# which is an argument for never tuning it, not for trading it.
S3_APPROVED_SLEEVES = ("rev", "dual")
S3_APPROVED_ASSETS = ("SPY_REV", "QQQ_REV", "GEM")


def all_symbols() -> list:
    """Every symbol any sleeve needs — the data layer's fetch list."""
    syms = {c["symbol"] for c in STOCK_TREND_ASSETS.values()}
    syms |= {c["symbol"] for c in STOCK_REV_ASSETS.values()}
    syms |= set(STOCK_DUAL_CONFIG["risk_assets"])
    syms |= {STOCK_DUAL_CONFIG["safe_asset"], STOCK_DUAL_CONFIG["cash_asset"]}
    return sorted(syms)
