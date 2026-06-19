"""Phase G — Donchian breakout signal helpers.

Pure functions:
  - compute_donchian_channels(df, period) → (upper, lower) Series
  - analyze_breakout_entry(df, cfg) → dict (parallel shape to analyze_entry_signal)
  - check_breakout_exit(df, direction, entry, atr, adx, cfg) → (reason, kind)

The strategy: enter when price closes outside the N-bar Donchian channel
(default 20) in a strong-trend regime (ATR > ATR_SMA, ADX > 20). Exit on
M-bar opposite-side cross (default 10), 1.5×ATR adverse move, or ADX
dropping below 15. Per plan G — fills the silent-momentum gap when BTC
isn't uptrending but a specific alt is.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore

logger = logging.getLogger("crypto_bot.breakout_signals")


# ─── Donchian channels ─────────────────────────────────────────────────────

def compute_donchian_channels(df, period: int):
    """Return (upper, lower) — rolling N-bar high/low Series.

    The upper is the highest HIGH over the trailing `period` bars; the
    lower is the lowest LOW. Both NaN for the first period-1 bars.
    """
    upper = df["high"].rolling(window=period, min_periods=period).max()
    lower = df["low"].rolling(window=period, min_periods=period).min()
    return upper, lower


# ─── Entry ─────────────────────────────────────────────────────────────────

def analyze_breakout_entry(df, cfg: dict, df_1d=None) -> dict:
    """Verbose entry-signal analyzer, parallel-shape to signals.analyze_entry_signal.

    Returns dict with would_enter, blocked_by, filters, values, direction.
    Direction is "LONG" or "SHORT" when would_enter is True; None otherwise.

    Phase G.2 added two optional gates:
      - volume confirmation (use_volume_filter=True): breakout bar volume
        > volume_threshold_mult × SMA(volume, volume_sma_period)
      - 1D trend gate (use_trend_filter=True): df_1d must have ema_fast/slow
        columns; LONG passes when ema_fast > ema_slow, SHORT mirrors

    df_1d may be None when use_trend_filter is False or when 1D data is
    unavailable — the gate defaults to pass when data is missing.
    """
    result = {
        "would_enter": False,
        "blocked_by":  None,
        "direction":   None,
        "filters":     {"donchian": None, "atr_regime": None, "adx": None,
                         "volume": None, "trend_1d": None},
        "values":      {},
    }

    period = cfg.get("donchian_period", 20)
    if df is None or len(df) < period + 1:
        result["blocked_by"] = "insufficient_data"
        return result

    upper, lower = compute_donchian_channels(df, period)
    curr = df.iloc[-1]
    # The "channel value" we compare close against is the channel BEFORE the
    # breakout bar (otherwise close would always be inside its own bar's
    # extreme). Use the prior bar's channel value.
    prior_upper = upper.iloc[-2]
    prior_lower = lower.iloc[-2]

    close   = float(curr["close"])
    atr     = float(curr.get("atr", 0) or 0)
    atr_sma = float(curr.get("atr_sma", 0) or 0)
    adx     = float(curr.get("adx", 0) or 0)

    result["values"] = {
        "close":       close,
        "upper":       float(prior_upper) if not pd.isna(prior_upper) else None,
        "lower":       float(prior_lower) if not pd.isna(prior_lower) else None,
        "atr":         atr,
        "atr_sma":     atr_sma,
        "adx":         adx,
    }

    # 1. Donchian break
    long_break  = (not pd.isna(prior_upper)) and close > prior_upper
    short_break = (not pd.isna(prior_lower)) and close < prior_lower
    direction = None
    if long_break:
        direction = "LONG"
    elif short_break:
        direction = "SHORT"

    if direction is None:
        result["filters"]["donchian"] = False
        result["blocked_by"] = "donchian"
        return result
    result["filters"]["donchian"] = True

    # 1b. SHORT enablement gate
    if direction == "SHORT" and not cfg.get("allow_short", False):
        result["blocked_by"] = "allow_short_disabled"
        return result

    # 2. ATR regime (high vol)
    atr_ok = atr > atr_sma and atr_sma > 0
    result["filters"]["atr_regime"] = atr_ok
    if not atr_ok:
        result["blocked_by"] = "atr_regime"
        return result

    # 3. ADX (trend strength)
    adx_ok = adx > cfg.get("adx_threshold", 20)
    result["filters"]["adx"] = adx_ok
    if not adx_ok:
        result["blocked_by"] = "adx"
        return result

    # 4. Volume confirmation (G.2)
    if cfg.get("use_volume_filter", False):
        vol_window = cfg.get("volume_sma_period", 20)
        threshold_mult = cfg.get("volume_threshold_mult", 1.5)
        if "volume" in df.columns and len(df) >= vol_window + 1:
            vol_sma = df["volume"].iloc[-vol_window - 1:-1].mean()
            current_vol = float(df.iloc[-1]["volume"])
            vol_ok = bool(vol_sma > 0 and current_vol > threshold_mult * vol_sma)
            result["filters"]["volume"] = vol_ok
            result["values"]["volume"] = current_vol
            result["values"]["vol_sma"] = float(vol_sma)
            if not vol_ok:
                result["blocked_by"] = "volume"
                return result

    # 5. 1D trend gate (G.2). Missing df_1d → pass (don't block on no-data).
    if cfg.get("use_trend_filter", False) and df_1d is not None and len(df_1d) > 0:
        if "ema_fast" in df_1d.columns and "ema_slow" in df_1d.columns:
            last_1d = df_1d.iloc[-1]
            ef = float(last_1d["ema_fast"])
            es = float(last_1d["ema_slow"])
            trend_ok = bool(ef > es if direction == "LONG" else ef < es)
            result["filters"]["trend_1d"] = trend_ok
            result["values"]["ema_fast_1d"] = ef
            result["values"]["ema_slow_1d"] = es
            if not trend_ok:
                result["blocked_by"] = "trend_1d"
                return result

    result["would_enter"] = True
    result["direction"] = direction
    return result


# ─── Exit ──────────────────────────────────────────────────────────────────

def check_breakeven_trigger(close: float, entry_price: float,
                              atr_at_entry: float, direction: str,
                              cfg: dict) -> bool:
    """Phase L.3.1 — has price moved enough favorably to ratchet the SL to
    breakeven?

    Returns True iff:
      - `use_breakeven_after_tp1` is True in cfg
      - Position has moved favorably by `breakeven_trigger_atr × ATR_at_entry`
        (default 1.0 ATR — same scale signals.py uses for its tp1_atr_mult
        baseline)

    Caller persists the resulting boolean on the position dict so the
    next check_breakout_exit invocation tightens the SL to entry price.
    Once True, stays True for the life of the position — never un-ratchets.
    """
    if not cfg.get("use_breakeven_after_tp1", False):
        return False
    trigger_atr = float(cfg.get("breakeven_trigger_atr", 1.0))
    threshold_distance = trigger_atr * atr_at_entry
    is_long = direction.upper() == "LONG"
    favorable_move = (close - entry_price) if is_long else (entry_price - close)
    return favorable_move >= threshold_distance


def check_breakout_exit(
    df,
    position_direction: str,
    entry_price: float,
    atr_at_entry: float,
    current_adx: float,
    cfg: dict,
    breakeven_triggered: bool = False,
) -> Tuple[Optional[str], Optional[str]]:
    """Return (reason, kind) or (None, None).

    Three exit rules per plan G:
      1. Donchian opposite-side cross over the EXIT period (default 10)
      2. Adverse move ≥ sl_atr_mult × ATR_at_entry (default 2.5)
      3. ADX drops below adx_exit_threshold (default 15) — trend dying

    Phase L.3.1: when `breakeven_triggered=True` (favorable move ≥ 1×
    ATR has already happened), SL ratchets to entry_price ("BE Hit")
    instead of staying at the wider entry_price - sl_mult × ATR. This
    converts would-be losers-after-a-winner into break-even exits.
    Mirrors signals.py:217-219 BTC_1D pattern.
    """
    if df is None or len(df) < cfg.get("donchian_exit_period", 10):
        return None, None

    exit_period = cfg.get("donchian_exit_period", 10)
    upper, lower = compute_donchian_channels(df, exit_period)
    curr  = df.iloc[-1]
    close = float(curr["close"])

    prior_upper = upper.iloc[-2]
    prior_lower = lower.iloc[-2]
    is_long = position_direction.upper() == "LONG"

    # 1. SL (sl_mult × ATR adverse) — or breakeven if L.3.1 triggered.
    sl_mult = cfg.get("sl_atr_mult", 2.0)
    if breakeven_triggered:
        sl_price = entry_price
        sl_reason = "BE Hit"
    elif is_long:
        sl_price = entry_price - sl_mult * atr_at_entry
        sl_reason = "SL Hit"
    else:
        sl_price = entry_price + sl_mult * atr_at_entry
        sl_reason = "SL Hit"

    if is_long:
        if close <= sl_price:
            return sl_reason, "full"
    else:
        if close >= sl_price:
            return sl_reason, "full"

    # 2. Donchian opposite-side cross
    if is_long and not pd.isna(prior_lower) and close < prior_lower:
        return "Donchian Exit", "full"
    if not is_long and not pd.isna(prior_upper) and close > prior_upper:
        return "Donchian Exit", "full"

    # 3. ADX dying
    adx_exit = cfg.get("adx_exit_threshold", 15)
    if current_adx is not None and current_adx < adx_exit:
        return "ADX Exit", "full"

    return None, None
