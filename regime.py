"""Regime classifier — labels a market snapshot by trend / vol / strength.

Phase B.3a of the comprehensive enhancement plan.

The momentum bot already computes EMA20/EMA50/EMA200, ATR, ATR_SMA, and ADX.
This module turns those scalars into a single regime label the dashboard
heatmap and per-trade journal tagging can consume.

Label set (6):
    strong_up        trend up + ADX above threshold
    weak_up          trend up + ADX below threshold
    strong_down      trend down + ADX above threshold
    weak_down        trend down + ADX below threshold
    range_high_vol   sideways + ATR > ATR_SMA
    range_low_vol    sideways + ATR < ATR_SMA

Trend direction (strict):
    up    = close > ema20 > ema50 > ema200
    down  = close < ema20 < ema50 < ema200
    flat  = anything else

Any missing input (None / NaN) → label "unknown" (caller decides what to
do; classify_regime() doesn't raise on missing data).

Also emits short-code variants suitable for the journal columns:
    btc_trend_code        "UP" / "DOWN" / None      (column: btc_trend_at_entry)
    atr_regime_code       "HIGH" / "LOW" / None     (column: atr_regime_at_entry)
"""

from __future__ import annotations

from typing import Optional


def _is_nan_or_none(x) -> bool:
    """True for None and for NaN floats (without importing math)."""
    if x is None:
        return True
    try:
        return x != x   # NaN
    except TypeError:
        return False


def classify_regime_from_values(
    close,
    ema20,
    ema50,
    ema200,
    atr,
    atr_sma,
    adx,
    adx_strong_threshold: float = 20.0,
) -> dict:
    """Pure scalar-input classifier. No pandas dependency.

    Returns: {
        "trend":            "up" | "down" | "flat" | "unknown",
        "vol":              "high" | "low" | "unknown",
        "strength":         "strong" | "weak" | "unknown",
        "label":            one of the six labels (or "unknown"),
        "btc_trend_code":   "UP" | "DOWN" | None,
        "atr_regime_code":  "HIGH" | "LOW" | None,
    }

    If any input is missing, the resulting label is "unknown" — caller
    decides whether to skip, retry, or store the null.
    """
    if any(_is_nan_or_none(v) for v in (close, ema20, ema50, ema200, atr, atr_sma, adx)):
        return {
            "trend": "unknown", "vol": "unknown", "strength": "unknown",
            "label": "unknown", "btc_trend_code": None, "atr_regime_code": None,
        }

    if close > ema20 > ema50 > ema200:
        trend = "up"
    elif close < ema20 < ema50 < ema200:
        trend = "down"
    else:
        trend = "flat"

    vol = "high" if atr > atr_sma else "low"
    strength = "strong" if adx >= adx_strong_threshold else "weak"

    if trend == "up":
        label = "strong_up" if strength == "strong" else "weak_up"
    elif trend == "down":
        label = "strong_down" if strength == "strong" else "weak_down"
    else:
        label = "range_high_vol" if vol == "high" else "range_low_vol"

    btc_trend_code = {"up": "UP", "down": "DOWN"}.get(trend)  # flat → None
    atr_regime_code = "HIGH" if vol == "high" else "LOW"

    return {
        "trend": trend, "vol": vol, "strength": strength,
        "label": label,
        "btc_trend_code": btc_trend_code,
        "atr_regime_code": atr_regime_code,
    }


def classify_regime(df, adx_strong_threshold: float = 20.0) -> dict:
    """DataFrame wrapper — reads the last completed bar.

    Expects columns: close, ema20, ema50, ema200, atr, atr_sma, adx
    (the standard set computed by signals.compute_indicators).

    Uses df.iloc[-2] — the last COMPLETED bar — same convention as the
    rest of the bot (df.iloc[-1] is the in-progress bar and shouldn't
    drive decisions).
    """
    if df is None or len(df) < 2:
        return classify_regime_from_values(
            None, None, None, None, None, None, None,
            adx_strong_threshold=adx_strong_threshold,
        )
    row = df.iloc[-2]
    return classify_regime_from_values(
        close=_safe_float(row.get("close")),
        ema20=_safe_float(row.get("ema20")),
        ema50=_safe_float(row.get("ema50")),
        ema200=_safe_float(row.get("ema200")),
        atr=_safe_float(row.get("atr")),
        atr_sma=_safe_float(row.get("atr_sma")),
        adx=_safe_float(row.get("adx")),
        adx_strong_threshold=adx_strong_threshold,
    )


def _safe_float(x) -> Optional[float]:
    """Coerce to float; return None for None or NaN."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN → None
