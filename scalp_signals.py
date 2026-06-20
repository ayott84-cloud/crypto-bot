"""Phase M — scalp bot signal logic.

Volatility-expansion + momentum + N-bar breakout, with a fixed
percentage SL/TP bracket. Strategy descends from Welles Wilder's
Volatility System (1978) + Larry Williams' trend-day filters.

Long entry (ALL true on the last COMPLETED 5m candle, df.iloc[-2]):
  1. SMA(range, 10) > SMA(range, 50)              (volatility expanding)
  2. close > close[20 bars ago]                   (momentum positive)
  3. close > highest close of the prior 20 bars   (new 20-bar high)
  4. close > open                                  (green body confirms direction)

Short = mirror (vol expanding + momentum down + new 20-bar low + red body).

Exits are pure percentage brackets — no ATR scaling:
  LONG : SL at entry × (1 - sl_pct/100), TP at entry × (1 + tp_pct/100)
  SHORT: mirror
"""

from __future__ import annotations

from typing import Optional

try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore


def analyze_scalp_entry(df, cfg: dict) -> dict:
    """Verbose entry-signal analyzer. Returns the same shape as
    `breakout_signals.analyze_breakout_entry` so the bot's main loop
    can reuse the existing per-asset signal_status pipeline.

    Returns:
      {
        "would_enter": bool,
        "blocked_by":  str | None,   first failing filter's key
        "direction":   "LONG" | "SHORT" | None,
        "filters":     dict[name -> True/False/None],
        "values":      dict[name -> current_value],
      }
    """
    result = {
        "would_enter": False,
        "blocked_by":  None,
        "direction":   None,
        "filters":     {"vol_expansion": None, "momentum": None,
                          "new_high": None, "candle_color": None},
        "values":      {},
    }

    short_window = int(cfg.get("range_short_sma", 10))
    long_window  = int(cfg.get("range_long_sma", 50))
    momo_n       = int(cfg.get("momentum_lookback", 20))
    high_n       = int(cfg.get("new_high_lookback", 20))

    needed = max(long_window, momo_n, high_n) + 2  # +2 for completed-bar offset
    if df is None or len(df) < needed:
        result["blocked_by"] = "insufficient_data"
        return result

    range_series = df["high"] - df["low"]
    range_sma_short = range_series.rolling(short_window).mean().iloc[-2]
    range_sma_long  = range_series.rolling(long_window).mean().iloc[-2]

    last = df.iloc[-2]            # last COMPLETED bar
    close       = float(last["close"])
    bar_open    = float(last["open"])
    close_n_ago = float(df["close"].iloc[-2 - momo_n])
    # Prior N bars EXCLUDING the completed bar itself
    closes_lookback = df["close"].iloc[-2 - high_n:-2]
    prior_max = float(closes_lookback.max())
    prior_min = float(closes_lookback.min())

    result["values"] = {
        "close":           close,
        "open":            bar_open,
        "close_n_ago":     close_n_ago,
        "prior_max":       prior_max,
        "prior_min":       prior_min,
        "range_sma_short": float(range_sma_short) if not _isnan(range_sma_short) else None,
        "range_sma_long":  float(range_sma_long)  if not _isnan(range_sma_long)  else None,
    }

    # 1. Volatility expansion (shared between LONG and SHORT)
    vol_expanding = (
        not _isnan(range_sma_short)
        and not _isnan(range_sma_long)
        and range_sma_short > range_sma_long
    )
    result["filters"]["vol_expansion"] = vol_expanding
    if not vol_expanding:
        result["blocked_by"] = "vol_expansion"
        return result

    # 2. Direction selection: momentum + new-extreme + body-color must all agree
    momentum_up   = close > close_n_ago
    momentum_down = close < close_n_ago
    new_high      = close > prior_max
    new_low       = close < prior_min
    green         = close > bar_open
    red           = close < bar_open

    if momentum_up and new_high and green:
        direction = "LONG"
    elif momentum_down and new_low and red:
        direction = "SHORT"
    else:
        # Identify the FIRST failing filter for blocked_by reporting.
        # Priority: momentum → new_high → candle_color (matches signal order).
        result["filters"]["momentum"] = momentum_up or momentum_down
        if not (momentum_up or momentum_down):
            result["blocked_by"] = "momentum"
        elif momentum_up and not new_high:
            result["filters"]["new_high"] = False
            result["blocked_by"] = "new_high"
        elif momentum_down and not new_low:
            result["filters"]["new_high"] = False
            result["blocked_by"] = "new_high"
        elif momentum_up and new_high and not green:
            result["filters"]["candle_color"] = False
            result["blocked_by"] = "candle_color"
        elif momentum_down and new_low and not red:
            result["filters"]["candle_color"] = False
            result["blocked_by"] = "candle_color"
        return result

    # 3. SHORT enablement gate — per-asset allow_short must be True
    if direction == "SHORT" and not cfg.get("allow_short", False):
        result["blocked_by"] = "allow_short_disabled"
        return result

    result["filters"]["momentum"]     = True
    result["filters"]["new_high"]     = True
    result["filters"]["candle_color"] = True
    result["would_enter"] = True
    result["direction"]   = direction
    return result


def check_scalp_exit(entry_price: float, current_price: float,
                       direction: str, cfg: dict) -> Optional[str]:
    """Pure percentage bracket: SL at -sl_pct%, TP at +tp_pct%.

    Returns "SL Hit" / "TP Hit" / None. Caller is responsible for
    flattening the position when a reason is returned.
    """
    sl_pct = float(cfg.get("sl_pct", 1.5))
    tp_pct = float(cfg.get("tp_pct", 3.0))
    if direction.upper() == "LONG":
        if current_price <= entry_price * (1.0 - sl_pct / 100.0):
            return "SL Hit"
        if current_price >= entry_price * (1.0 + tp_pct / 100.0):
            return "TP Hit"
    else:
        if current_price >= entry_price * (1.0 + sl_pct / 100.0):
            return "SL Hit"
        if current_price <= entry_price * (1.0 - tp_pct / 100.0):
            return "TP Hit"
    return None


# ─── helpers ──────────────────────────────────────────────────────────

def _isnan(v) -> bool:
    if v is None:
        return True
    try:
        return v != v
    except TypeError:
        return False
