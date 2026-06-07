"""Donchian breakout bot configuration (Phase G).

Donchian breakout fills the silent-momentum gap: when BTC isn't trending
and the EMA-crossover momentum bot stands aside, individual alts can
still print clean N-bar highs/lows with strong ADX. Different signal
events than the momentum bot — diversifies the fleet.

Defaults follow plan G.1 + the public-pattern backtest research:
  - Donchian 20 on 4h / Daily timeframes
  - SHORT off by default; flip per asset after backtest validates
  - PAUSED by default until backtest + 60 days paper validate
"""

from __future__ import annotations

import os
from pathlib import Path

_BOT_DIR = Path(__file__).resolve().parent

# ─── Master pause flag ─────────────────────────────────────────────────────
# Set BREAKOUT_PAUSED=false in .env after backtest + paper validation.
# Defaults TRUE so deploying the code never opens a live position
# accidentally.
BREAKOUT_PAUSED = os.getenv("BREAKOUT_PAUSED", "true").lower() in ("true", "1", "yes")

# ─── Polling cadence ───────────────────────────────────────────────────────
# Same as momentum: 5-minute poll. Each asset's own timeframe (4h, 1d)
# governs how often a real signal CAN fire.
BREAKOUT_POLL_INTERVAL_SECONDS = 300

# ─── State + naming ────────────────────────────────────────────────────────
BREAKOUT_STATE_KEY_PREFIX = "BREAKOUT_"
BREAKOUT_STRATEGY_TAG = "Breakout"  # for log_trade + dashboard bot column

# Heartbeat file the dashboard checks for LIVE/STALE/NEVER pill.
BREAKOUT_HEARTBEAT_FILE = _BOT_DIR / ".breakout_heartbeat"

# ─── Position sizing + caps ────────────────────────────────────────────────
# Half-size during validation per plan F/G pattern. Lift after paper PF >= 1.5.
BREAKOUT_MARGIN_PER_TRADE = 25.0
BREAKOUT_LEVERAGE = 10
# Max concurrent breakout positions. Consumes slots out of the global
# MAX_POSITIONS pool (currently 8); cap at 2 during validation.
MAX_BREAKOUT_POSITIONS = 2

# ─── Per-asset configs ─────────────────────────────────────────────────────
# Defaults below are the plan G baseline. Override per asset once
# TradingView backtest validates. Add/remove assets here as needed.
BREAKOUT_ASSETS = {
    "BTC_4H": {
        "symbol":               "BTCUSDT",
        "interval":             "4h",
        "donchian_period":      55,   # G.2: Turtle System 1 (was 20)
        "donchian_exit_period": 20,   # G.2: Turtle exit (was 10)
        "atr_period":           14,
        "atr_sma_period":       20,
        "adx_period":           14,
        "adx_threshold":        20,    # entry gate
        "adx_exit_threshold":   15,    # exit trigger (trend dying)
        "sl_atr_mult":          2.5,
        "use_volume_filter":     True,    # G.2 — require >1.5x SMA volume
        "volume_threshold_mult": 1.5,
        "volume_sma_period":     20,
        "use_trend_filter":      True,    # G.2 — require 1D EMA20/50 agree   # widened from 1.5 after 2C.3 backtest showed noise-stop-outs
        "allow_short":          True,    # G.2: enabled — strategy is symmetric, restricting to LONG-only kills it in downtrends  # off until per-asset backtest
        "sl_atr_mult_short":    1.0,    # tighter SL for shorts
        "strategy_name":        "BTC 4H Breakout",
        "use_btc_filter":       False,  # breakout IS the directional signal
    },
    "ETH_4H": {
        "symbol":               "ETHUSDT",
        "interval":             "4h",
        "donchian_period":      55,   # G.2: Turtle System 1 (was 20)
        "donchian_exit_period": 20,   # G.2: Turtle exit (was 10)
        "atr_period":           14,
        "atr_sma_period":       20,
        "adx_period":           14,
        "adx_threshold":        20,
        "adx_exit_threshold":   15,
        "sl_atr_mult":          2.5,
        "use_volume_filter":     True,    # G.2 — require >1.5x SMA volume
        "volume_threshold_mult": 1.5,
        "volume_sma_period":     20,
        "use_trend_filter":      True,    # G.2 — require 1D EMA20/50 agree   # widened from 1.5 after 2C.3 backtest showed noise-stop-outs
        "allow_short":          True,    # G.2: enabled — strategy is symmetric, restricting to LONG-only kills it in downtrends
        "sl_atr_mult_short":    1.0,
        "strategy_name":        "ETH 4H Breakout",
        "use_btc_filter":       False,
    },
    "SOL_4H": {
        "symbol":               "SOLUSDT",
        "interval":             "4h",
        "donchian_period":      55,   # G.2: Turtle System 1 (was 20)
        "donchian_exit_period": 20,   # G.2: Turtle exit (was 10)
        "atr_period":           14,
        "atr_sma_period":       20,
        "adx_period":           14,
        "adx_threshold":        20,
        "adx_exit_threshold":   15,
        "sl_atr_mult":          2.5,
        "use_volume_filter":     True,    # G.2 — require >1.5x SMA volume
        "volume_threshold_mult": 1.5,
        "volume_sma_period":     20,
        "use_trend_filter":      True,    # G.2 — require 1D EMA20/50 agree   # widened from 1.5 after 2C.3 backtest showed noise-stop-outs
        "allow_short":          True,    # G.2: enabled — strategy is symmetric, restricting to LONG-only kills it in downtrends
        "sl_atr_mult_short":    1.0,
        "strategy_name":        "SOL 4H Breakout",
        "use_btc_filter":       False,
    },
    # Phase K (Jun 2026) — promoted from BREAKOUT_CANDIDATE_ASSETS after
    # tools/validate_breakout_candidates.py cleared the gates:
    #   BTC_1H  PF=4.40  n=6  WR=50.0%  total=+13.2%  DD=3.0%
    #   ETH_1H  PF=2.14  n=7  WR=28.6%  total=+12.2%  DD=7.1%
    # Faster TF — 55-bar Donchian on 1h = ~2.3 days. Catches moves the
    # 4H configs sleep through.
    "BTC_1H": {
        "symbol":               "BTCUSDT",
        "interval":             "1h",
        "donchian_period":      55,
        "donchian_exit_period": 20,
        "atr_period":           14,
        "atr_sma_period":       20,
        "adx_period":           14,
        "adx_threshold":        20,
        "adx_exit_threshold":   15,
        "sl_atr_mult":          2.5,
        "use_volume_filter":     True,
        "volume_threshold_mult": 1.5,
        "volume_sma_period":     20,
        "use_trend_filter":      True,
        "allow_short":          True,
        "sl_atr_mult_short":    1.0,
        "strategy_name":        "BTC 1H Breakout",
        "use_btc_filter":       False,
    },
    "ETH_1H": {
        "symbol":               "ETHUSDT",
        "interval":             "1h",
        "donchian_period":      55,
        "donchian_exit_period": 20,
        "atr_period":           14,
        "atr_sma_period":       20,
        "adx_period":           14,
        "adx_threshold":        20,
        "adx_exit_threshold":   15,
        "sl_atr_mult":          2.5,
        "use_volume_filter":     True,
        "volume_threshold_mult": 1.5,
        "volume_sma_period":     20,
        "use_trend_filter":      True,
        "allow_short":          True,
        "sl_atr_mult_short":    1.0,
        "strategy_name":        "ETH 1H Breakout",
        "use_btc_filter":       False,
    },
}


# ─── Phase K second round (Jun 7 2026) — 5 1H alts cleared the gate ──────
# from the top-30 candidate pass (1000-bar replay):
#   DOGE_1H  PF=1.93  n=6  WR=50.0%  total= +7.0%  DD=7.6%
#   ADA_1H   PF=3.25  n=5  WR=40.0%  total=+19.5%  DD=8.4%
#   NEAR_1H  PF=6.46  n=5  WR=60.0%  total=+38.4%  DD=7.0%
#   AAVE_1H  PF=2.39  n=5  WR=40.0%  total=+14.1%  DD=8.4%
#   INJ_1H   PF=2.31  n=6  WR=66.7%  total=+10.5%  DD=6.7%
# Same baseline shape as BTC_1H / ETH_1H (Donchian 55/20, sl_atr_mult 2.5,
# volume + 1D-trend filters on, allow_short on). Updated AFTER the dict
# is defined so we don't introduce forward-reference issues.
for _name, _symbol, _title in [
    ("DOGE_1H", "DOGEUSDT", "DOGE 1H Breakout"),
    ("ADA_1H",  "ADAUSDT",  "ADA 1H Breakout"),
    ("NEAR_1H", "NEARUSDT", "NEAR 1H Breakout"),
    ("AAVE_1H", "AAVEUSDT", "AAVE 1H Breakout"),
    ("INJ_1H",  "INJUSDT",  "INJ 1H Breakout"),
]:
    BREAKOUT_ASSETS[_name] = {
        "symbol":                _symbol,
        "interval":              "1h",
        "donchian_period":       55,
        "donchian_exit_period":  20,
        "atr_period":            14,
        "atr_sma_period":        20,
        "adx_period":            14,
        "adx_threshold":         20,
        "adx_exit_threshold":    15,
        "sl_atr_mult":           2.5,
        "use_volume_filter":      True,
        "volume_threshold_mult":  1.5,
        "volume_sma_period":      20,
        "use_trend_filter":       True,
        "allow_short":           True,
        "sl_atr_mult_short":     1.0,
        "strategy_name":         _title,
        "use_btc_filter":        False,
    }
del _name, _symbol, _title


# ─── Phase K — candidate assets (NOT live until promoted) ────────────────
# Per the activation gate flow: stage candidates here, run
# tools/validate_breakout_candidates.py on the droplet (needs WEEX
# kline access), promote passing rows by moving them into BREAKOUT_ASSETS
# above. BREAKOUT_CANDIDATE_ASSETS is never iterated by breakout_main —
# only by the validator.
#
# Each candidate inherits the same shape as BREAKOUT_ASSETS rows.
# Defaults match the proven BTC_4H baseline (Donchian 55/20, ATR 14/20,
# ADX 20/15, sl_atr_mult 2.5, volume + 1D-trend filters ON,
# allow_short ON).
def _breakout_default(symbol: str, interval: str, name: str) -> dict:
    """Build a candidate config row using the validated 4H baseline."""
    return {
        "symbol":                symbol,
        "interval":              interval,
        "donchian_period":       55,
        "donchian_exit_period":  20,
        "atr_period":            14,
        "atr_sma_period":        20,
        "adx_period":            14,
        "adx_threshold":         20,
        "adx_exit_threshold":    15,
        "sl_atr_mult":           2.5,
        "use_volume_filter":      True,
        "volume_threshold_mult":  1.5,
        "volume_sma_period":      20,
        "use_trend_filter":       True,
        "allow_short":           True,
        "sl_atr_mult_short":     1.0,
        "strategy_name":         name,
        "use_btc_filter":        False,
    }


# Top-30 universe per whale_universe._FALLBACK_TOP_SYMBOLS (Apr 2026).
# Already-promoted (BTC, ETH, SOL) excluded from the auto-expansion.
# WEEX-unsupported symbols dropped from the Jun 2026 run:
#   - MATIC: WEEX trades POLUSDT (Polygon rebranded)
#   - SHIB:  symbol format differs (1000SHIB or kSHIB depending on venue)
#   - PEPE:  same — 1000PEPE on most CEX
# BTC_1W also dropped: WEEX 1W history depth too shallow for 55-bar Donchian.
_TOP30_NEW_ALTS = [
    ("XRP",    "XRPUSDT"),    ("DOGE",   "DOGEUSDT"),
    ("ADA",    "ADAUSDT"),    ("TRX",    "TRXUSDT"),
    ("DOT",    "DOTUSDT"),    ("LTC",    "LTCUSDT"),
    ("NEAR",   "NEARUSDT"),   ("UNI",    "UNIUSDT"),
    ("FIL",    "FILUSDT"),    ("ETC",    "ETCUSDT"),
    ("APT",    "APTUSDT"),    ("ARB",    "ARBUSDT"),
    ("ATOM",   "ATOMUSDT"),   ("SUI",    "SUIUSDT"),
    ("HBAR",   "HBARUSDT"),   ("AAVE",   "AAVEUSDT"),
    ("OP",     "OPUSDT"),     ("INJ",    "INJUSDT"),
    ("RENDER", "RENDERUSDT"), ("TON",    "TONUSDT"),
    ("ICP",    "ICPUSDT"),
]


def _expand_top30(tf: str) -> dict:
    """Build {NAME_TF: cfg} for every top-30 alt + the retained BNB/AVAX/LINK
    at the given timeframe ("4h" or "1h"). 1H gives 4× more bars on the
    same 1000-cap window so low-frequency assets get more potential
    trades."""
    extras = [("BNB", "BNBUSDT"), ("AVAX", "AVAXUSDT"), ("LINK", "LINKUSDT")]
    suffix = tf.upper()
    return {
        f"{name}_{suffix}": _breakout_default(
            symbol, tf, f"{name} {suffix} Breakout")
        for name, symbol in (extras + _TOP30_NEW_ALTS)
    }


_PROMOTED_KEYS = {"DOGE_1H", "ADA_1H", "NEAR_1H", "AAVE_1H", "INJ_1H"}

BREAKOUT_CANDIDATE_ASSETS = {
    # 4H variants — slow steady trend signal
    **_expand_top30("4h"),
    # 1H variants — 4× more bars on same WEEX limit; gives low-frequency
    # alts more potential trades. BTC_1H + ETH_1H already promoted earlier;
    # DOGE/ADA/NEAR/AAVE/INJ 1H promoted in the second round (see above).
    **{k: v for k, v in _expand_top30("1h").items()
        if k not in _PROMOTED_KEYS},
}
# Promotion history (BREAKOUT_ASSETS, in order):
#   BTC_4H, ETH_4H, SOL_4H — original G.2 baseline
#   BTC_1H, ETH_1H — commit 998af1c
#   DOGE_1H, ADA_1H, NEAR_1H, AAVE_1H, INJ_1H — this commit


# ─── Phase J.6 — backtest stats for projection table ──────────────────────
# Source: tools/backtest_replay.py 1000-bar 4h replay (Jun 2026). Each
# row's `trades` count is genuinely small; the projection table renders
# these as "low confidence" so the operator doesn't over-weight them.
BREAKOUT_BACKTEST_STATS = {
    "BTC_4H": {"pf": 2.81, "trades":  4, "pnl_pct": 11.5, "dd_pct": 6.0,
                "wr": 50.0, "years": 0.46,
                "source": "1000-bar 4h replay (small n)"},
    "ETH_4H": {"pf": 3.17, "trades":  3, "pnl_pct": 21.5, "dd_pct": 9.9,
                "wr": 66.7, "years": 0.46,
                "source": "1000-bar 4h replay (small n)"},
    "SOL_4H": {"pf": 91.83, "trades": 2, "pnl_pct": 23.5, "dd_pct": 0.3,
                "wr": 50.0, "years": 0.46,
                "source": "1000-bar 4h replay (n=2 — directional only)"},
    # Phase K (Jun 2026) — promoted candidates
    "BTC_1H": {"pf": 4.40, "trades":  6, "pnl_pct": 13.2, "dd_pct": 3.0,
                "wr": 50.0, "years": 0.11,
                "source": "1000-bar 1h replay"},
    "ETH_1H": {"pf": 2.14, "trades":  7, "pnl_pct": 12.2, "dd_pct": 7.1,
                "wr": 28.6, "years": 0.11,
                "source": "1000-bar 1h replay"},
    # Phase K second round (Jun 7 2026) — 1H alts
    "DOGE_1H": {"pf": 1.93, "trades": 6, "pnl_pct":  7.0, "dd_pct": 7.6,
                 "wr": 50.0, "years": 0.11, "source": "1000-bar 1h replay"},
    "ADA_1H":  {"pf": 3.25, "trades": 5, "pnl_pct": 19.5, "dd_pct": 8.4,
                 "wr": 40.0, "years": 0.11, "source": "1000-bar 1h replay"},
    "NEAR_1H": {"pf": 6.46, "trades": 5, "pnl_pct": 38.4, "dd_pct": 7.0,
                 "wr": 60.0, "years": 0.11, "source": "1000-bar 1h replay"},
    "AAVE_1H": {"pf": 2.39, "trades": 5, "pnl_pct": 14.1, "dd_pct": 8.4,
                 "wr": 40.0, "years": 0.11, "source": "1000-bar 1h replay"},
    "INJ_1H":  {"pf": 2.31, "trades": 6, "pnl_pct": 10.5, "dd_pct": 6.7,
                 "wr": 66.7, "years": 0.11, "source": "1000-bar 1h replay"},
}
