"""Phase M — scalp bot signal logic.

Volatility-expansion + momentum + N-bar breakout, with a fixed
percentage SL/TP bracket. Strategy descends from Welles Wilder's
Volatility System (1978) + Larry Williams' trend-day filters.

Baseline signal (the original Phase M.1 spec):
  1. SMA(range, 10) > SMA(range, 50)              (volatility expanding)
  2. close > close[20 bars ago]                   (momentum positive)
  3. close > highest close of the prior 20 bars   (new 20-bar high)
  4. close > open                                  (green body for LONG)

Phase M.2 enhancement filters (all default OFF in the analyzer; cfg
turns them ON for live + validator use):
  5. Higher-TF trend gate: 1h EMA20 > EMA50 (LONG) — kills counter-trend
  6. Volume confirmation: current bar volume > vol_threshold_mult × SMA(volume, 20)
  7. Stronger vol expansion: SMA10_range > expansion_threshold × SMA50_range
  8. RSI extreme filter: block LONG if RSI > rsi_overbought, SHORT if RSI < rsi_oversold

Each filter can be toggled independently via per-asset cfg flags. The
analyzer reports the FIRST failing filter as blocked_by, in the order
the gates are checked.

Exits stay pure-percentage: -sl_pct% / +tp_pct% (default 1.5 / 3.0).
"""

from __future__ import annotations

from typing import Optional


def analyze_scalp_entry(df, cfg: dict, df_1h=None) -> dict:
    """Verbose entry-signal analyzer. Returns the same shape as
    `breakout_signals.analyze_breakout_entry` so the bot's main loop
    can reuse the existing per-asset signal_status pipeline.

    df_1h is the 1-hour DataFrame for the higher-TF trend gate (Phase
    M.2). Pass None when not available — the gate defaults to pass
    when data is missing (graceful degradation, mirrors breakout_main's
    1D pattern).

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
                          "new_high": None, "candle_color": None,
                          "volume": None, "trend_1h": None, "rsi": None},
        "values":      {},
    }

    short_window = int(cfg.get("range_short_sma", 10))
    long_window  = int(cfg.get("range_long_sma", 50))
    momo_n       = int(cfg.get("momentum_lookback", 20))
    high_n       = int(cfg.get("new_high_lookback", 20))
    vol_threshold_mult = float(cfg.get("vol_threshold_mult", 1.5))
    vol_sma_period     = int(cfg.get("vol_sma_period", 20))
    rsi_period         = int(cfg.get("rsi_period", 14))
    rsi_overbought     = float(cfg.get("rsi_overbought", 70.0))
    rsi_oversold       = float(cfg.get("rsi_oversold", 30.0))
    # Phase M.2 baseline relaxed for backwards-compat with M.1 tests:
    # threshold defaults to 1.0 (same as the original > comparison).
    # Per-asset cfg can tighten to 1.5 (the M.2 enhancement).
    expansion_threshold = float(cfg.get("vol_expansion_threshold", 1.0))

    needed = max(long_window, momo_n, high_n,
                  vol_sma_period, rsi_period) + 2
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

    # 1. Volatility expansion — threshold defaults to 1.0 (original
    # behavior) but M.2 baseline cfg tightens to 1.5.
    if (_isnan(range_sma_short) or _isnan(range_sma_long)
            or range_sma_long <= 0):
        vol_expanding = False
    else:
        vol_expanding = range_sma_short > expansion_threshold * range_sma_long
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

    # 3. SHORT enablement gate
    if direction == "SHORT" and not cfg.get("allow_short", False):
        result["blocked_by"] = "allow_short_disabled"
        return result

    result["filters"]["momentum"]     = True
    result["filters"]["new_high"]     = True
    result["filters"]["candle_color"] = True

    # ── Phase M.2 enhancement filters (all optional, OFF by default) ──

    # 4. Volume confirmation: current bar volume > vol_threshold_mult × SMA
    if cfg.get("use_volume_filter", False) and "volume" in df.columns:
        vol_sma = df["volume"].rolling(vol_sma_period).mean().iloc[-2]
        current_vol = float(last["volume"])
        if (_isnan(vol_sma) or vol_sma <= 0
                or current_vol <= vol_threshold_mult * float(vol_sma)):
            result["filters"]["volume"] = False
            result["blocked_by"] = "volume"
            return result
        result["filters"]["volume"] = True
        result["values"]["volume"] = current_vol
        result["values"]["volume_sma"] = float(vol_sma)

    # 5. Higher-TF trend gate (1h EMA20 vs EMA50)
    if cfg.get("use_higher_tf_trend", False) and df_1h is not None and len(df_1h) >= 50:
        if "ema_fast" in df_1h.columns and "ema_slow" in df_1h.columns:
            last_1h = df_1h.iloc[-1]
            ef = float(last_1h["ema_fast"])
            es = float(last_1h["ema_slow"])
            trend_ok = (ef > es) if direction == "LONG" else (ef < es)
            result["filters"]["trend_1h"] = trend_ok
            result["values"]["ema_fast_1h"] = ef
            result["values"]["ema_slow_1h"] = es
            if not trend_ok:
                result["blocked_by"] = "trend_1h"
                return result

    # 6. RSI extreme filter (avoid LONG into overbought, SHORT into oversold)
    if cfg.get("use_rsi_extreme_filter", False):
        rsi_val = _compute_rsi(df["close"], rsi_period)
        if rsi_val is not None:
            result["values"]["rsi"] = rsi_val
            if direction == "LONG" and rsi_val > rsi_overbought:
                result["filters"]["rsi"] = False
                result["blocked_by"] = "rsi_overbought"
                return result
            if direction == "SHORT" and rsi_val < rsi_oversold:
                result["filters"]["rsi"] = False
                result["blocked_by"] = "rsi_oversold"
                return result
            result["filters"]["rsi"] = True

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


def _compute_rsi(close_series, period: int = 14) -> Optional[float]:
    """Standard Wilder-smoothed RSI on the closes Series. Returns the
    RSI value on the last completed bar (iloc[-2]). None when there
    isn't enough history."""
    if close_series is None or len(close_series) < period + 2:
        return None
    delta = close_series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.rolling(period).mean()
    avg_loss = losses.rolling(period).mean()
    avg_loss_last = avg_loss.iloc[-2]
    if _isnan(avg_loss_last) or avg_loss_last == 0:
        return 100.0  # all gains, no losses → overbought sentinel
    rs = avg_gain.iloc[-2] / avg_loss_last
    if _isnan(rs):
        return None
    return float(100.0 - (100.0 / (1.0 + rs)))


def _isnan(v) -> bool:
    if v is None:
        return True
    try:
        return v != v
    except TypeError:
        return False
