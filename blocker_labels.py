"""Human-readable labels for signal `blocked_by` values.

Phase B.2. The momentum bot's `analyze_entry_signal` (signals.py) attributes
the first failing filter to `blocked_by` using short keys ("btc_filter",
"trend", "rsi_crossover", etc). The dashboard previously rendered those
keys verbatim — operators had to remember what each one meant.

This module provides one-sentence labels for the dashboard. Keep BLOCKER_LABELS
in sync with the `fail("…")` keys in signals.py; tests verify coverage so
drift fails CI rather than landing silently.
"""

from __future__ import annotations

from typing import Optional


BLOCKER_LABELS: dict[str, str] = {
    "insufficient_data":  "Not enough kline history for indicators",
    "nan_indicators":     "Indicator series contains NaNs",
    "trend":              "EMA-fast not above EMA-slow",
    "close_above_ema":    "Close not above the configured EMA",
    "atr_regime":         "Low-volatility regime (ATR below SMA)",
    "rsi_crossover":      "RSI not crossing above its SMA",
    "macd":               "MACD histogram not positive",
    "pmo":                "PMO not above its signal line",
    "volume":             "Volume below its moving average",
    "mfi":                "MFI not in the bullish band",
    "adx":                "ADX trend strength below threshold",
    "btc_filter":         "BTC below EMA — alt correlation gate",
}


def blocker_label(blocked_by: Optional[str]) -> str:
    """Render a `blocked_by` value as a human sentence.

    None / "" → "" (not blocked, nothing to say).
    Known key → the canonical label from BLOCKER_LABELS.
    Unknown key → echo it verbatim (don't lose information when a new key
    is added to signals.py before this map is updated).
    """
    if not blocked_by:
        return ""
    return BLOCKER_LABELS.get(blocked_by, blocked_by)
