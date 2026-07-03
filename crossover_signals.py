"""Phase N — crossover bot signal logic.

Classic dual SMA crossover on 5m closes. Entry fires ONLY on the bar
where SMA(close, fast) crosses SMA(close, slow). Subsequent bars in
the same regime are NOT re-entries (mirrors the academic "Golden
Cross / Death Cross" trigger semantics, not "always in market").

Signal:
  LONG  iff prev SMA_fast <= prev SMA_slow AND curr SMA_fast > curr SMA_slow
  SHORT iff prev SMA_fast >= prev SMA_slow AND curr SMA_fast < curr SMA_slow

Where "curr" = the last completed bar (iloc[-2]) and "prev" = the
bar before that (iloc[-3]). iloc[-1] is the still-forming bar and is
ignored — mirrors the scalp + breakout convention.

Exits: pure pct bracket, -sl_pct% / +tp_pct% (default 1% / 2% = 1:2 R/R,
breakeven WR 33.3% before fees).

No filters by default. Operator can wire higher-TF trend / volume /
RSI gates per-asset via cfg flags if a baseline backtest shows lack
of edge.
"""

from __future__ import annotations

from typing import Optional


def analyze_crossover_entry(df, cfg: dict, df_1h=None) -> dict:
    """Verbose entry-signal analyzer.

    df_1h is the 1-hour DataFrame for the higher-TF trend gate (Phase
    N.2). Pass None when not available — the gate defaults to pass
    (graceful degradation, mirrors scalp_signals/breakout_main patterns).

    Returns:
      {
        "would_enter": bool,
        "blocked_by":  str | None,
        "direction":   "LONG" | "SHORT" | None,
        "filters":     dict[name -> True/False/None],
        "values":      dict[name -> current_value],
      }
    """
    result = {
        "would_enter": False,
        "blocked_by":  None,
        "direction":   None,
        "filters":     {"crossover": None, "trend_1h": None},
        "values":      {},
    }

    fast_n = int(cfg.get("sma_fast", 50))
    slow_n = int(cfg.get("sma_slow", 100))

    # SMA(slow_n) first becomes valid at index slow_n - 1. We evaluate
    # prev at iloc[-3] (index len-3) and curr at iloc[-2] (index len-2).
    # Need len-3 >= slow_n - 1  ⇒  len >= slow_n + 2.
    needed = slow_n + 2
    if df is None or len(df) < needed:
        result["blocked_by"] = "insufficient_data"
        return result

    sma_fast = df["close"].rolling(fast_n).mean()
    sma_slow = df["close"].rolling(slow_n).mean()

    curr_fast = sma_fast.iloc[-2]
    curr_slow = sma_slow.iloc[-2]
    prev_fast = sma_fast.iloc[-3]
    prev_slow = sma_slow.iloc[-3]

    if any(_isnan(v) for v in (curr_fast, curr_slow, prev_fast, prev_slow)):
        result["blocked_by"] = "insufficient_data"
        return result

    result["values"] = {
        "sma_fast":      float(curr_fast),
        "sma_slow":      float(curr_slow),
        "sma_fast_prev": float(prev_fast),
        "sma_slow_prev": float(prev_slow),
    }

    long_cross  = (prev_fast <= prev_slow) and (curr_fast > curr_slow)
    short_cross = (prev_fast >= prev_slow) and (curr_fast < curr_slow)

    if long_cross:
        direction = "LONG"
    elif short_cross:
        direction = "SHORT"
    else:
        result["filters"]["crossover"] = False
        result["blocked_by"] = "no_crossover"
        return result

    if direction == "SHORT" and not cfg.get("allow_short", True):
        result["blocked_by"] = "allow_short_disabled"
        return result

    result["filters"]["crossover"] = True

    # ── Phase N.3: 200-SMA trend filter with slope gate ──
    # TradingRush 100-trade template: losses cluster when the 200 SMA is
    # flat/sideways. LONG only when price above a RISING SMA200; SHORT
    # only below a FALLING one. Insufficient data → pass.
    if cfg.get("use_sma200_filter", False) and len(df) >= 205:
        sma200 = df["close"].rolling(200).mean()
        s_now = sma200.iloc[-2]
        s_then = sma200.iloc[-7]   # 5-bar slope lookback
        close_now = float(df["close"].iloc[-2])
        if not (_isnan(s_now) or _isnan(s_then)):
            slope_ok = (s_now > s_then) if direction == "LONG" else (s_now < s_then)
            side_ok = (close_now > s_now) if direction == "LONG" else (close_now < s_now)
            result["values"]["sma200"] = float(s_now)
            if not (slope_ok and side_ok):
                result["filters"]["sma200"] = False
                result["blocked_by"] = "sma200_filter"
                return result
            result["filters"]["sma200"] = True

    # ── Phase N.3: ADX trend-strength gate ──
    # Vanilla crossover has no edge in chop (40-57% false-signal rate);
    # ADX > threshold restricts entries to trending conditions.
    if cfg.get("use_adx_filter", False):
        adx_val = _compute_adx(df, int(cfg.get("adx_period", 14)))
        if adx_val is not None:
            result["values"]["adx"] = adx_val
            if adx_val < float(cfg.get("adx_threshold", 20.0)):
                result["filters"]["adx"] = False
                result["blocked_by"] = "adx"
                return result
            result["filters"]["adx"] = True

    # ── Phase N.2: higher-TF trend gate (1h EMA20 vs EMA50) ──
    # Mirrors scalp_signals.analyze_scalp_entry. Defaults OFF; flip ON
    # via cfg["use_higher_tf_trend"]. When df_1h is missing or too short,
    # the gate defaults to pass (graceful degradation).
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

    result["would_enter"] = True
    result["direction"] = direction
    return result


def check_crossover_exit(entry_price: float, current_price: float,
                            direction: str, cfg: dict) -> Optional[str]:
    """Pure percentage bracket: SL at -sl_pct%, TP at +tp_pct%.

    Returns "SL Hit" / "TP Hit" / None.
    """
    sl_pct = float(cfg.get("sl_pct", 1.0))
    tp_pct = float(cfg.get("tp_pct", 2.0))
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


def check_crossover_exit_v3(direction: str, entry_price: float,
                               current_close: float,
                               sma_fast_now,
                               atr_at_entry,
                               cfg: dict) -> Optional[str]:
    """Phase N.3 exit — signal invalidation, not a tight bracket.

    A 1h SMA20/50 cross is a multi-day trend signal; a -1% stop exits it
    on the first adverse hourly wick (the N.2 failure: ETH/XRP 0-for-10).
    N.3 exits when the signal is no longer true:

      Invalidation: close crosses back through SMA-fast against the
                    position (LONG: close < sma_fast; SHORT: close > it)
      Emergency SL: price moves emergency_atr_mult x ATR-at-entry against
                    the position — pure gap protection, rarely touched

    sma_fast_now=None (data hiccup) → only the emergency stop applies.
    Returns "Invalidation Exit" / "Emergency SL" / None.
    """
    # Emergency stop first — must work even without SMA data
    if atr_at_entry and atr_at_entry > 0:
        mult = float(cfg.get("emergency_atr_mult", 3.5))
        if direction == "LONG" and current_close <= entry_price - mult * atr_at_entry:
            return "Emergency SL"
        if direction == "SHORT" and current_close >= entry_price + mult * atr_at_entry:
            return "Emergency SL"

    if sma_fast_now is None or _isnan(sma_fast_now):
        return None
    if direction == "LONG" and current_close < float(sma_fast_now):
        return "Invalidation Exit"
    if direction == "SHORT" and current_close > float(sma_fast_now):
        return "Invalidation Exit"
    return None


def _compute_adx(df, period: int = 14) -> Optional[float]:
    """Wilder ADX on the last completed bar. None when insufficient data
    or pandas_ta unavailable."""
    if df is None or len(df) < period * 3:
        return None
    try:
        import pandas_ta as ta
        adx_df = ta.adx(df["high"], df["low"], df["close"], length=period)
        col = f"ADX_{period}"
        if adx_df is None or col not in adx_df.columns:
            return None
        val = adx_df[col].iloc[-2]
        return None if _isnan(val) else float(val)
    except Exception:  # noqa: BLE001
        return None


def _isnan(v) -> bool:
    if v is None:
        return True
    try:
        return v != v
    except TypeError:
        return False
