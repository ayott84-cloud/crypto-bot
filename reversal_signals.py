"""Phase I — RSI-VWAP Extreme Reversal signals.

Implements Alex Carter Trading's spec (extracted from Whop product
screenshots):

  RSI VWAP (length 15) computed on session VWAP instead of close.
  Extreme Reversal Setup: 3× average-range capitulation candle detector.

  LONG  when RSI(VWAP) < 10 AND rising AND bullish dot (close low) — same bar
  SHORT when RSI(VWAP) > 90 AND falling AND bearish dot (close high) — same bar

Public functions:
  compute_vwap(df)                  → Series (anchored intraday VWAP)
  compute_rsi_vwap(df, length)      → Series
  is_extreme_bar(df, mult, sma_len) → bool
  reversal_dot_polarity(df, pct)    → "bullish" | "bearish" | None
  analyze_reversal_entry(df, cfg, rsi_vwap_series=None) → dict
"""

from __future__ import annotations

import logging
from typing import Optional

try:
    import pandas as pd
    import numpy as np
except ImportError:
    pd = None
    np = None

logger = logging.getLogger("crypto_bot.reversal_signals")


# ─── VWAP + RSI(VWAP) ──────────────────────────────────────────────────────

def compute_vwap(df, window: int | None = None):
    """Volume-weighted average price.

    window=None → cumulative-from-genesis (only meaningful for
                   session-anchored intraday data where caller slices
                   per-session).
    window=N    → rolling-N-bar VWAP. Use this on Daily/Weekly data
                   where there's no natural session reset; otherwise the
                   cumulative VWAP grows monotonically with trend and
                   delta(VWAP) becomes one-sided, pinning RSI near 100.

    Default in compute_rsi_vwap below is window=20 (Bollinger-style
    anchor) which keeps RSI(VWAP) meaningfully oscillating on any TF.
    """
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].astype(float)
    tp_vol = tp * vol
    if window is None:
        cum_tp_vol = tp_vol.cumsum()
        cum_vol = vol.cumsum()
    else:
        cum_tp_vol = tp_vol.rolling(window=window, min_periods=1).sum()
        cum_vol = vol.rolling(window=window, min_periods=1).sum()
    return cum_tp_vol / cum_vol.where(cum_vol > 0)


def compute_rsi_vwap(df, length: int = 15, vwap_window: int = 20):
    """RSI computed against the VWAP series (not close prices).

    vwap_window=20 by default — rolling VWAP so RSI(VWAP) oscillates
    across the full 0-100 range. The earlier cumulative-VWAP impl pinned
    RSI near 100 because cumulative VWAP grows monotonically with trend.

    Set vwap_window=None to recover the old session-anchored behavior
    if the caller has already sliced data per-session.
    """
    vwap = compute_vwap(df, window=vwap_window).ffill()
    delta = vwap.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder's smoothing = EMA with alpha = 1/length
    avg_gain = gain.ewm(alpha=1.0 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # When avg_loss==0 (monotonic up), RS is infinite → RSI = 100.
    # When avg_gain==0 (monotonic down), RS = 0 → RSI = 0.
    rsi = rsi.where(~(avg_loss == 0), 100.0)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss > 0)), 0.0)
    return rsi


# ─── Extreme Reversal Setup ───────────────────────────────────────────────

def is_extreme_bar(df, range_mult: float = 3.0,
                    range_sma_length: int = 14,
                    bar_offset: int = 0) -> bool:
    """True when the bar at `-1 - bar_offset` has range >= range_mult × SMA(range, length).

    bar_offset=0 → check the latest bar. bar_offset=1 → check one bar back.
    Used by the v2 window-bar logic to scan for the extreme event up to
    N bars in the past."""
    if df is None or len(df) < range_sma_length + 1 + bar_offset:
        return False
    bar_range = (df["high"] - df["low"]).astype(float)
    # SMA computed over the `range_sma_length` bars preceding the target bar
    end_idx = len(df) - 1 - bar_offset
    if end_idx < range_sma_length:
        return False
    avg = bar_range.iloc[end_idx - range_sma_length:end_idx].mean()
    last_range = float(bar_range.iloc[end_idx])
    if avg <= 0:
        return False
    return bool(last_range >= range_mult * avg)


def reversal_dot_polarity(df, close_position_pct: float = 0.30,
                           bar_offset: int = 0):
    """Where in the latest bar's range does the close sit?

    Returns "bullish" if close is in the bottom `close_position_pct`
    (sellers exhausted — buyers stepping in to defend lows).
    Returns "bearish" if close is in the top `close_position_pct`
    (buyers exhausted — sellers stepping in to fade highs).
    Returns None when close is somewhere in the middle.
    """
    if df is None or len(df) == 0:
        return None
    idx = len(df) - 1 - bar_offset
    if idx < 0:
        return None
    bar = df.iloc[idx]
    high = float(bar["high"])
    low  = float(bar["low"])
    close = float(bar["close"])
    rng = high - low
    if rng <= 0:
        return None
    position = (close - low) / rng  # 0.0 = low, 1.0 = high
    if position <= close_position_pct:
        return "bullish"
    if position >= 1.0 - close_position_pct:
        return "bearish"
    return None


# ─── Entry analyzer ───────────────────────────────────────────────────────

def analyze_reversal_entry(df, cfg: dict, rsi_vwap_series=None) -> dict:
    """Return entry decision dict. rsi_vwap_series override is for testing —
    in production we compute it from the dataframe."""
    result = {
        "would_enter": False,
        "blocked_by":  None,
        "direction":   None,
        "filters":     {
            "extreme_bar": None, "dot": None,
            "rsi_extreme": None, "cloud": None,
        },
        "values": {},
    }

    need = max(cfg.get("range_sma_length", 14), cfg.get("rsi_length", 15)) + 4
    if df is None or len(df) < need:
        result["blocked_by"] = "insufficient_data"
        return result

    rsi = (rsi_vwap_series
           if rsi_vwap_series is not None
           else compute_rsi_vwap(df, length=cfg.get("rsi_length", 15)))
    if rsi is None or len(rsi) < 2 or pd.isna(rsi.iloc[-1]) or pd.isna(rsi.iloc[-2]):
        result["blocked_by"] = "insufficient_data"
        return result

    curr_rsi = float(rsi.iloc[-1])
    prev_rsi = float(rsi.iloc[-2])
    result["values"]["rsi_curr"] = curr_rsi
    result["values"]["rsi_prev"] = prev_rsi

    # 1. Extreme bar — scan back `window_bars` bars for the event.
    #    v1.0 looked at -1 only; v2 allows the conjunction within a 1-3 bar
    #    window because the dot rarely lines up exactly with RSI extreme.
    window_bars = max(1, int(cfg.get("window_bars", 3)))
    extreme_offset = None
    polarity = None
    for offset in range(window_bars):
        if is_extreme_bar(df,
                           range_mult=cfg.get("range_mult", 3.0),
                           range_sma_length=cfg.get("range_sma_length", 14),
                           bar_offset=offset):
            pol_at_offset = reversal_dot_polarity(
                df,
                close_position_pct=cfg.get("close_position_pct", 0.30),
                bar_offset=offset)
            if pol_at_offset is not None:
                extreme_offset = offset
                polarity = pol_at_offset
                break

    extreme = extreme_offset is not None
    result["filters"]["extreme_bar"] = extreme
    if not extreme:
        result["blocked_by"] = "no_extreme_bar"
        return result

    result["filters"]["dot"] = polarity
    result["values"]["dot"] = polarity
    result["values"]["extreme_offset"] = extreme_offset

    # Decide direction. Both RSI side AND dot polarity must agree.
    oversold = cfg.get("oversold", 10.0)
    overbought = cfg.get("overbought", 90.0)

    # LONG side: RSI < oversold AND rising AND bullish dot
    if curr_rsi < oversold:
        result["filters"]["rsi_extreme"] = True
        rising = curr_rsi > prev_rsi
        result["filters"]["cloud"] = "green" if rising else "red"
        if not rising:
            result["blocked_by"] = "wrong_cloud"
            return result
        if polarity != "bullish":
            result["blocked_by"] = "wrong_dot"
            return result
        if not cfg.get("allow_long", True):
            result["blocked_by"] = "allow_long_disabled"
            return result
        result["would_enter"] = True
        result["direction"] = "LONG"
        return result

    # SHORT side: RSI > overbought AND falling AND bearish dot
    if curr_rsi > overbought:
        result["filters"]["rsi_extreme"] = True
        falling = curr_rsi < prev_rsi
        result["filters"]["cloud"] = "red" if falling else "green"
        if not falling:
            result["blocked_by"] = "wrong_cloud"
            return result
        if polarity != "bearish":
            result["blocked_by"] = "wrong_dot"
            return result
        if not cfg.get("allow_short", True):
            result["blocked_by"] = "allow_short_disabled"
            return result
        result["would_enter"] = True
        result["direction"] = "SHORT"
        return result

    result["filters"]["rsi_extreme"] = False
    result["blocked_by"] = "rsi_not_extreme"
    return result
