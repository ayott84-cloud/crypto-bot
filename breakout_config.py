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
        # Jul 16 2026 trailing A/B (same-window, 4 arms, n=71, ~4.9y):
        # early_arm won decisively — PF 1.04→1.54, DD 64.3%→22.2%,
        # total +6.3%→+71.0%. Arms at 1.0×ATR favorable, trails 1.0×ATR.
        "use_trailing_exit":    True,
        "trail_arm_atr_mult":   1.0,
        "trail_atr_mult":       1.0,
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
        "use_regime_gate":       True,   # L.2 (Phase K promotion)
        "use_breakeven_after_tp1": True,  # L.3.1 (Phase K promotion)
        "breakeven_trigger_atr":   1.0,
        "allow_pyramiding":        True,  # L.3.3 (Phase K promotion)
        "max_pyramid_legs":        2,
        "pyramid_trigger_atr":     1.0,
        "pyramid_size_fraction":   0.5,
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
        "use_regime_gate":       True,   # L.2 (Phase K promotion)
        "use_breakeven_after_tp1": True,  # L.3.1 (Phase K promotion)
        "breakeven_trigger_atr":   1.0,
        "allow_pyramiding":        True,  # L.3.3 (Phase K promotion)
        "max_pyramid_legs":        2,
        "pyramid_trigger_atr":     1.0,
        "pyramid_size_fraction":   0.5,
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
        "use_regime_gate":        True,   # L.2 (Phase K promotion)
        "use_breakeven_after_tp1": True,  # L.3.1 (Phase K promotion)
        "breakeven_trigger_atr":   1.0,
        "allow_pyramiding":        True,  # L.3.3 (Phase K promotion)
        "max_pyramid_legs":        2,
        "pyramid_trigger_atr":     1.0,
        "pyramid_size_fraction":   0.5,
        "allow_short":           True,
        "sl_atr_mult_short":     1.0,
        "strategy_name":         _title,
        "use_btc_filter":        False,
    }
del _name, _symbol, _title

# ─── Phase K round 3 (Jun 7 2026) — D20 recovery passers ────────────────
# Turtle System 2 (Donchian 20/10) generated enough signals to clear the
# gate where the baseline 55/20 had n<5:
#   BNB_4H_D20  PF=2.33  n=9  WR=44.4%  total=+14.2%  DD=7.7%
#   TRX_4H_D20  PF=3.82  n=6  WR=50.0%  total= +6.0%  DD=0.9%
#   BNB_1H_D20  PF=5.27  n=8  WR=62.5%  total=+12.3%  DD=2.5%
for _name, _symbol, _interval, _title in [
    ("BNB_4H_D20", "BNBUSDT", "4h", "BNB 4H Breakout (D20)"),
    ("TRX_4H_D20", "TRXUSDT", "4h", "TRX 4H Breakout (D20)"),
    ("BNB_1H_D20", "BNBUSDT", "1h", "BNB 1H Breakout (D20)"),
]:
    BREAKOUT_ASSETS[_name] = {
        "symbol":                _symbol,
        "interval":              _interval,
        "donchian_period":       20,   # Turtle System 2
        "donchian_exit_period":  10,
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
        "use_regime_gate":        True,   # L.2 (Phase K D20 recovery)
        "use_breakeven_after_tp1": True,  # L.3.1 (Phase K D20 recovery)
        "breakeven_trigger_atr":   1.0,
        "allow_pyramiding":        True,  # L.3.3 (Phase K D20 recovery)
        "max_pyramid_legs":        2,
        "pyramid_trigger_atr":     1.0,
        "pyramid_size_fraction":   0.5,
        "allow_short":           True,
        "sl_atr_mult_short":     1.0,
        "strategy_name":         _title,
        "use_btc_filter":        False,
    }
del _name, _symbol, _interval, _title


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
    """Build a candidate config row using the validated 4H baseline.

    L.2 default: use_regime_gate ON for any asset built via this factory
    (covers all Phase K promotions + candidates). The 3 legacy assets
    (BTC_4H, ETH_4H, SOL_4H) defined as inline dicts above don't get
    this default — their flag remains absent (= False) to preserve
    their original behavior until each is individually backtested with
    the gate on.
    """
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
        "use_regime_gate":        True,   # L.2
        "use_breakeven_after_tp1": True,  # L.3.1
        "breakeven_trigger_atr":   1.0,
        "allow_pyramiding":        True,  # L.3.3
        "max_pyramid_legs":        2,
        "pyramid_trigger_atr":     1.0,
        "pyramid_size_fraction":   0.5,
        # ── P3.3 (Jul 2026 research upgrades) ──
        # Funding veto: skip entries INTO the crowded side (funding
        # ≥ +0.05%/8h blocks LONG; ≤ -0.05%/8h blocks SHORT).
        "use_funding_veto":        True,
        "funding_veto_threshold":  0.0005,
        # Offset-armed trailing exit: arms at 1.5×ATR favorable move,
        # then trails 1×ATR off the water mark — protects the runners
        # that pay for a low-WR breakout strategy.
        "use_trailing_exit":       True,
        "trail_arm_atr_mult":      1.5,
        "trail_atr_mult":          1.0,
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


_PROMOTED_KEYS = {
    # Round 2 (Jun 7, commit 86253c4)
    "DOGE_1H", "ADA_1H", "NEAR_1H", "AAVE_1H", "INJ_1H",
    # Round 3 D20 recovery (Jun 7, this commit)
    "BNB_4H_D20", "TRX_4H_D20", "BNB_1H_D20",
}


# Recovery variant: Donchian-20 / exit-10 (Turtle System 2). The 55/20
# baseline generates ~1 signal per 4 months at 4H — too sparse for the
# n>=5 gate over a 1000-bar window. Donchian-20 with the same
# volume + 1D-trend + ADX gates produces ~3× signal density while
# maintaining selectivity.
#
# Targeting Category A: assets that showed strong PF (>2.5) but failed
# n<5 on the 55/20 round. If Donchian-20 lifts them above n>=5, they
# promote. Naming suffix `_D20` distinguishes from the baseline.
_DONCHIAN20_RECOVERY_TARGETS_4H = [
    ("BNB",  "BNBUSDT"),   # 55/20 was PF=13.89 n=3
    ("AVAX", "AVAXUSDT"),  #         PF=inf   n=1
    ("LINK", "LINKUSDT"),  #         PF=inf   n=1
    ("DOGE", "DOGEUSDT"),  #         PF=4.41  n=3
    ("ADA",  "ADAUSDT"),   #         PF=inf   n=1
    ("TRX",  "TRXUSDT"),   #         PF=4.77  n=4 — one trade short
    ("DOT",  "DOTUSDT"),   #         PF=inf   n=2
    ("LTC",  "LTCUSDT"),   #         PF=inf   n=2
    ("UNI",  "UNIUSDT"),   #         PF=7.22  n=2
    ("FIL",  "FILUSDT"),   #         PF=3.71  n=2
    ("APT",  "APTUSDT"),   #         PF=inf   n=3
    ("ARB",  "ARBUSDT"),   #         PF=3.73  n=2
    ("ATOM", "ATOMUSDT"),  #         PF=inf   n=1
    ("SUI",  "SUIUSDT"),   #         PF=inf   n=1
    ("HBAR", "HBARUSDT"),  #         PF=inf   n=1
    ("OP",   "OPUSDT"),    #         PF=inf   n=2
    ("TON",  "TONUSDT"),   #         PF=4.51  n=3
    ("ICP",  "ICPUSDT"),   #         PF=inf   n=1
]

_DONCHIAN20_RECOVERY_TARGETS_1H = [
    ("BNB",  "BNBUSDT"),   # 55/20 was PF=4.03 n=4 — one trade short
]


def _breakout_donchian20(symbol: str, interval: str, name: str) -> dict:
    """Turtle System 2 variant: 20-bar entry / 10-bar exit. Same gates
    (volume + 1D-trend + ADX) so the signal density rises without
    losing selectivity entirely."""
    cfg = _breakout_default(symbol, interval, name)
    cfg["donchian_period"]      = 20
    cfg["donchian_exit_period"] = 10
    return cfg


def _expand_recovery_d20() -> dict:
    out = {}
    for name, symbol in _DONCHIAN20_RECOVERY_TARGETS_4H:
        out[f"{name}_4H_D20"] = _breakout_donchian20(
            symbol, "4h", f"{name} 4H Breakout (D20)")
    for name, symbol in _DONCHIAN20_RECOVERY_TARGETS_1H:
        out[f"{name}_1H_D20"] = _breakout_donchian20(
            symbol, "1h", f"{name} 1H Breakout (D20)")
    return out


BREAKOUT_CANDIDATE_ASSETS = {
    # 4H variants — slow steady trend signal (Donchian 55/20, Turtle Sys 1)
    **_expand_top30("4h"),
    # 1H variants — 4× more bars on same WEEX limit. BTC_1H + ETH_1H
    # already promoted earlier; DOGE/ADA/NEAR/AAVE/INJ 1H promoted in
    # round 2.
    **{k: v for k, v in _expand_top30("1h").items()
        if k not in _PROMOTED_KEYS},
    # Recovery: Donchian-20 (Turtle Sys 2) for assets that showed strong
    # PF on 55/20 but failed n<5. ~3× signal density at same gate level.
    **{k: v for k, v in _expand_recovery_d20().items()
        if k not in _PROMOTED_KEYS},
}
# Promotion history (BREAKOUT_ASSETS, in order):
#   BTC_4H, ETH_4H, SOL_4H — original G.2 baseline
#   BTC_1H, ETH_1H — commit 998af1c
#   DOGE_1H, ADA_1H, NEAR_1H, AAVE_1H, INJ_1H — round 2
#   BNB_4H_D20, TRX_4H_D20, BNB_1H_D20 — round 3 (D20 recovery)


# ─── P4 Step-2 demotions (Jul 4 2026, operator-approved) ──────────────────
# Long-window HONEST replay (17,000 bars ≈ 2yr at 1h / ~5-8yr at 4h,
# Coinbase, conservative fills + 0.15% costs + deployed exit stack)
# ruled these out. The short-window Phase K numbers that promoted them
# were regime luck on n<10 samples:
#   BTC_4H  PF=1.16 DD=57.6%   BTC_1H  PF=0.85   ADA_1H  PF=0.75
#   NEAR_1H PF=1.07 DD=62.7%   AAVE_1H PF=0.88
#   BNB/TRX D20: no US-reachable long-window data source — unvalidatable,
#   therefore no promotion path (TV Basic plan can't backfill either).
# KEPT (long-run positive expectancy): SOL_4H 1.43, ETH_4H 1.25,
# DOGE_1H 1.41, ETH_1H 1.24, INJ_1H 1.23 — paper-only research status;
# nothing cleared the PF>=1.5 promotion gate. Exit-management for any
# open position on a demoted asset keeps working via
# breakout_main._cfg_for_open_position's candidate-dict fallback.
_STEP2_DEMOTED = [
    "BTC_4H", "BTC_1H", "ADA_1H", "NEAR_1H", "AAVE_1H",
    "BNB_4H_D20", "TRX_4H_D20", "BNB_1H_D20",
]
for _k in _STEP2_DEMOTED:
    if _k in BREAKOUT_ASSETS:
        BREAKOUT_CANDIDATE_ASSETS[_k] = BREAKOUT_ASSETS.pop(_k)


# ─── Jul 16 2026 — trailing-exit A/B flips (tools/ab_breakout_trailing) ───
# Same-window 4-arm replays; switch rule: challenger PF >= live + 0.10,
# n >= 5. The A/B also exposed that NO live asset had use_trailing_exit
# set (live == no_trail identically) — the knob only existed in the
# candidate factory. ETH_4H flipped inline in its dict (early_arm won:
# PF 1.04->1.54, DD 64.3->22.2, n=71). INJ_1H below (wide_trail won:
# PF 1.05->1.17, total +8.7%->+30.8%, n=79). ETH_1H / DOGE_1H KEEP —
# early-armed trails whipsaw 1h noise (PF 0.91 / 0.89). SOL_4H HOLD
# pending a clean-window rerun (its A/B window truncated to 1.4y).
BREAKOUT_ASSETS["INJ_1H"].update({
    "use_trailing_exit":  True,
    "trail_arm_atr_mult": 1.5,
    "trail_atr_mult":     2.0,
})


# ─── Phase J.6 — backtest stats for projection table ──────────────────────
# P4 Step-2 refresh (Jul 4 2026): every row now cites the long-window
# HONEST replay (intra-bar fills, 0.15% costs, trailing + breakeven
# exits). The Phase K short-window numbers (PF 2.3-91.8 on n=2-9) are
# gone — they were the exact overfit the P4 pipeline exists to kill.
_BK_1H_SOURCE = "17000-bar 1h Coinbase honest replay (~2yr, deployed exits)"
_BK_4H_SOURCE = "17000-bar 4h Coinbase honest replay (multi-yr, deployed exits)"
BREAKOUT_BACKTEST_STATS = {
    # ── Live set (Step-2 keeps) ──
    "SOL_4H":  {"pf": 1.43, "trades":  46, "pnl_pct":  74.7, "dd_pct": 29.9,
                 "wr": 47.8, "years": 5.0, "source": _BK_4H_SOURCE},
    "ETH_4H":  {"pf": 1.54, "trades":  71, "pnl_pct":  71.0, "dd_pct": 22.2,
                 "wr": 60.6, "years": 4.9,
                 "source": "Jul 16 trailing A/B winning arm (early_arm, "
                            "10799-bar 4h Coinbase honest replay)"},
    "DOGE_1H": {"pf": 1.41, "trades":  90, "pnl_pct":  49.3, "dd_pct": 27.7,
                 "wr": 34.4, "years": 1.9, "source": _BK_1H_SOURCE},
    "ETH_1H":  {"pf": 1.24, "trades":  93, "pnl_pct":  32.6, "dd_pct": 31.4,
                 "wr": 31.2, "years": 1.9, "source": _BK_1H_SOURCE},
    "INJ_1H":  {"pf": 1.17, "trades":  79, "pnl_pct":  30.8, "dd_pct": 35.9,
                 "wr": 35.4, "years": 1.9,
                 "source": "Jul 16 trailing A/B winning arm (wide_trail, "
                            "17000-bar 1h Coinbase honest replay)"},
    # ── Demoted at Step-2 (kept for candidate-table honesty) ──
    "BTC_4H":  {"pf": 1.16, "trades":  78, "pnl_pct":  32.3, "dd_pct": 57.6,
                 "wr": 38.5, "years": 7.8,
                 "source": _BK_4H_SOURCE + " — DEMOTED"},
    "BTC_1H":  {"pf": 0.85, "trades": 106, "pnl_pct": -14.8, "dd_pct": 47.2,
                 "wr": 25.5, "years": 1.9,
                 "source": _BK_1H_SOURCE + " — DEMOTED"},
    "ADA_1H":  {"pf": 0.75, "trades": 105, "pnl_pct": -43.1, "dd_pct": 73.8,
                 "wr": 27.6, "years": 1.9,
                 "source": _BK_1H_SOURCE + " — DEMOTED"},
    "NEAR_1H": {"pf": 1.07, "trades":  92, "pnl_pct":  11.2, "dd_pct": 62.7,
                 "wr": 22.8, "years": 1.9,
                 "source": _BK_1H_SOURCE + " — DEMOTED"},
    "AAVE_1H": {"pf": 0.88, "trades": 102, "pnl_pct": -22.7, "dd_pct": 54.8,
                 "wr": 24.5, "years": 1.9,
                 "source": _BK_1H_SOURCE + " — DEMOTED"},
    # BNB/TRX D20: no honest long-window source exists (not US-listed);
    # short-window numbers retained ONLY as provenance, flagged unvalidated.
    "BNB_4H_D20": {"pf": 2.33, "trades": 9, "pnl_pct": 14.2, "dd_pct": 7.7,
                    "wr": 44.4, "years": 0.46,
                    "source": "1000-bar 4h replay — UNVALIDATED (no long-window source), DEMOTED"},
    "TRX_4H_D20": {"pf": 3.82, "trades": 6, "pnl_pct":  6.0, "dd_pct": 0.9,
                    "wr": 50.0, "years": 0.46,
                    "source": "1000-bar 4h replay — UNVALIDATED (no long-window source), DEMOTED"},
    "BNB_1H_D20": {"pf": 5.27, "trades": 8, "pnl_pct": 12.3, "dd_pct": 2.5,
                    "wr": 62.5, "years": 0.11,
                    "source": "1000-bar 1h replay — UNVALIDATED (no long-window source), DEMOTED"},
}
