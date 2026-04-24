"""Signal engine: indicator calculation and entry/exit logic.

Pure functions — no API calls, no side effects.
Uses pandas-ta for indicators, with manual PMO implementation.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta as ta
from typing import Optional, Tuple


def build_dataframe(raw_klines: list) -> pd.DataFrame:
    """Convert raw WEEX kline arrays into a pandas DataFrame.

    WEEX returns: [open_time, open, high, low, close, volume,
                   close_time, quote_vol, num_trades, taker_buy_vol, taker_buy_quote_vol]
    """
    columns = [
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "num_trades",
        "taker_buy_volume", "taker_buy_quote_volume",
    ]
    df = pd.DataFrame(raw_klines, columns=columns[:len(raw_klines[0])])

    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["close_time"] = pd.to_numeric(df["close_time"], errors="coerce")
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    return df


def compute_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Add all strategy indicator columns to the dataframe."""
    c = df["close"]
    h = df["high"]
    l = df["low"]  # noqa: E741

    # EMAs
    df["ema_fast"] = ta.ema(c, length=cfg["ema_fast"])
    df["ema_slow"] = ta.ema(c, length=cfg["ema_slow"])

    # ATR + ATR SMA
    df["atr"] = ta.atr(h, l, c, length=cfg["atr_period"])
    df["atr_sma"] = ta.sma(df["atr"], length=cfg["atr_sma_period"])

    # RSI + RSI SMA
    df["rsi"] = ta.rsi(c, length=cfg["rsi_period"])
    df["rsi_sma"] = ta.sma(df["rsi"], length=cfg["rsi_sma_period"])

    # MACD
    macd_df = ta.macd(c, fast=cfg["macd_fast"], slow=cfg["macd_slow"], signal=cfg["macd_signal"])
    if macd_df is not None:
        macd_cols = macd_df.columns
        df["macd_line"] = macd_df[macd_cols[0]]
        df["macd_signal_line"] = macd_df[macd_cols[1]]
        df["macd_hist"] = macd_df[macd_cols[2]]
    else:
        df["macd_line"] = float("nan")
        df["macd_signal_line"] = float("nan")
        df["macd_hist"] = float("nan")

    # PMO (manual — not in pandas-ta)
    if cfg.get("use_pmo", False):
        roc1 = c.pct_change(1) * 100  # 1-bar rate of change as percentage
        smoothed1 = ta.ema(roc1, length=cfg["pmo_ema1"])
        pmo_line = ta.ema(smoothed1, length=cfg["pmo_ema2"])
        if pmo_line is not None:
            df["pmo"] = pmo_line * 10
            df["pmo_signal"] = ta.ema(df["pmo"], length=cfg["pmo_signal_len"])
        else:
            df["pmo"] = float("nan")
            df["pmo_signal"] = float("nan")

    # Volume SMA
    if cfg.get("use_volume_filter", False):
        df["vol_sma"] = ta.sma(df["volume"], length=cfg.get("volume_sma_period", 20))

    # MFI (Money Flow Index) — volume-weighted RSI (v2 BTC enhancement)
    if cfg.get("use_mfi_filter", False):
        df["mfi"] = ta.mfi(h, l, c, df["volume"], length=cfg.get("mfi_period", 14))

    # ADX (Average Directional Index) — trend strength (v2 BTC enhancement)
    if cfg.get("use_adx_filter", False):
        adx_df = ta.adx(h, l, c, length=cfg.get("adx_period", 14))
        if adx_df is not None:
            # pandas-ta returns columns: ADX_{len}, DMP_{len}, DMN_{len}
            adx_col = [col for col in adx_df.columns if col.startswith("ADX_")]
            if adx_col:
                df["adx"] = adx_df[adx_col[0]]
            else:
                df["adx"] = float("nan")
        else:
            df["adx"] = float("nan")

    return df


def check_entry_signal(
    df: pd.DataFrame,
    cfg: dict,
    btc_close: Optional[float] = None,
    btc_ema: Optional[float] = None,
) -> bool:
    """Check if entry conditions are met on the LAST COMPLETED candle.

    Uses index -2 (second-to-last row) because the last row (-1)
    is the currently in-progress candle from WEEX.

    btc_close/btc_ema: optional BTC context for correlation filter.
    If cfg["use_btc_filter"] is True, both must be provided or signal fails.
    """
    if len(df) < 3:
        return False

    # Last completed candle = row at index -2
    curr = df.iloc[-2]
    prev = df.iloc[-3]

    # Guard against NaN in indicators (warmup period)
    required_cols = ["ema_fast", "ema_slow", "atr", "atr_sma", "rsi", "rsi_sma", "macd_hist"]
    for col in required_cols:
        if pd.isna(curr.get(col)) or pd.isna(prev.get(col)):
            return False

    # 1. Trend: EMA fast > EMA slow
    if curr["ema_fast"] <= curr["ema_slow"]:
        return False

    # 2. Price above EMA
    if cfg["close_above"] == "ema_fast":
        if curr["close"] <= curr["ema_fast"]:
            return False
    else:  # ema_slow (XRP)
        if curr["close"] <= curr["ema_slow"]:
            return False

    # 3. ATR regime: ATR > its SMA (trending/volatile market)
    if pd.isna(curr["atr_sma"]) or curr["atr"] <= curr["atr_sma"]:
        return False

    # 4. RSI crossover above its SMA + RSI in range
    rsi_cross = (curr["rsi"] > curr["rsi_sma"]) and (prev["rsi"] <= prev["rsi_sma"])
    rsi_in_range = cfg["rsi_min"] <= curr["rsi"] <= cfg["rsi_max"]
    if not (rsi_cross and rsi_in_range):
        return False

    # 5. MACD confirmation
    if cfg["macd_mode"] == "strict":
        if curr["macd_hist"] <= 0:
            return False
    else:  # loose (XRP): histogram > 0 OR macd_line > signal_line
        if not (curr["macd_hist"] > 0 or curr["macd_line"] > curr["macd_signal_line"]):
            return False

    # 6. PMO confirmation (BTC/ETH only)
    if cfg.get("use_pmo", False):
        if pd.isna(curr.get("pmo")) or pd.isna(curr.get("pmo_signal")):
            return False
        if curr["pmo"] <= curr["pmo_signal"]:
            return False

    # 7. Volume filter (XRP only)
    if cfg.get("use_volume_filter", False):
        threshold = cfg.get("volume_threshold", 0.8)
        if pd.isna(curr.get("vol_sma")) or curr["volume"] <= threshold * curr["vol_sma"]:
            return False

    # 8. MFI filter (BTC v2) — volume-weighted RSI conviction
    if cfg.get("use_mfi_filter", False):
        mfi_threshold = cfg.get("mfi_threshold", 50)
        if pd.isna(curr.get("mfi")) or curr["mfi"] <= mfi_threshold:
            return False

    # 9. ADX filter (BTC v2) — require strong trend
    if cfg.get("use_adx_filter", False):
        adx_threshold = cfg.get("adx_threshold", 20)
        if pd.isna(curr.get("adx")) or curr["adx"] <= adx_threshold:
            return False

    # 10. BTC correlation filter (ETH/XRP/SOL v2) — universal alt edge
    # Only long alts when BTC is above its EMA on the same timeframe.
    if cfg.get("use_btc_filter", False):
        if btc_close is None or btc_ema is None:
            return False  # filter requires data, fail closed
        if btc_close <= btc_ema:
            return False

    return True


def check_exit_conditions(
    entry_price: float,
    atr_at_entry: float,
    current_price: float,
    bars_since_entry: int,
    phase: str,
    cfg: dict,
) -> Tuple[Optional[str], Optional[str]]:
    """Check exit conditions. Returns (reason, exit_type) or (None, None).

    phase: "full" (no TP taken yet) or "tp1_taken" (50% closed at TP1).
    exit_type: "partial" for TP1, "full" for everything else.

    BTC v2 enhancement: After TP1, SL moves to breakeven (entry_price) if
    cfg["use_breakeven_after_tp1"] is True. This converts would-be losers
    into breakeven exits — major contributor to PF 2.163 vs 0.36.
    """
    tp1_price = entry_price + cfg["tp1_atr_mult"] * atr_at_entry
    tp2_price = entry_price + cfg["tp2_atr_mult"] * atr_at_entry

    # Dynamic SL: breakeven after TP1 if enabled, else original SL distance
    if phase == "tp1_taken" and cfg.get("use_breakeven_after_tp1", False):
        sl_price = entry_price  # breakeven stop
        sl_reason = "BE Hit"
    else:
        sl_price = entry_price - cfg["sl_atr_mult"] * atr_at_entry
        sl_reason = "SL Hit"

    # 1. Stop Loss / Breakeven stop — always checked first
    if current_price <= sl_price:
        return sl_reason, "full"

    # 2. TP1 — only if we haven't taken partial profit yet
    if phase == "full" and current_price >= tp1_price:
        return "TP1 Hit", "partial"

    # 3. TP2 — only after TP1 was taken
    if phase == "tp1_taken" and current_price >= tp2_price:
        return "TP2 Hit", "full"

    # 4. Stale exit — trade stuck in limbo
    stale_bars = cfg["stale_bars"]
    stale_mult = cfg["stale_threshold_mult"]
    stale_level = entry_price + atr_at_entry * cfg["tp1_atr_mult"] * stale_mult

    if bars_since_entry >= stale_bars and current_price < stale_level:
        return "Stale Exit", "full"

    return None, None


def analyze_entry_signal(
    df: pd.DataFrame,
    cfg: dict,
    btc_close: Optional[float] = None,
    btc_ema: Optional[float] = None,
) -> dict:
    """Verbose version of check_entry_signal — returns full diagnostic breakdown.

    Returns dict with structure:
    {
        "would_enter": bool,
        "blocked_by": str or None (first failed filter),
        "filters": {filter_name: True/False/None},  # None = not applicable (disabled)
        "values": {name: current_value},
    }
    """
    result = {
        "would_enter": False,
        "blocked_by": None,
        "filters": {
            "trend": None,
            "close_above_ema": None,
            "atr_regime": None,
            "rsi_crossover": None,
            "macd": None,
            "pmo": None,
            "volume": None,
            "mfi": None,
            "adx": None,
            "btc_filter": None,
        },
        "values": {},
    }

    if len(df) < 3:
        result["blocked_by"] = "insufficient_data"
        return result

    curr = df.iloc[-2]
    prev = df.iloc[-3]

    # Record current indicator values for display
    result["values"] = {
        "close": float(curr["close"]) if not pd.isna(curr.get("close")) else None,
        "ema_fast": float(curr["ema_fast"]) if not pd.isna(curr.get("ema_fast")) else None,
        "ema_slow": float(curr["ema_slow"]) if not pd.isna(curr.get("ema_slow")) else None,
        "atr": float(curr["atr"]) if not pd.isna(curr.get("atr")) else None,
        "atr_sma": float(curr["atr_sma"]) if not pd.isna(curr.get("atr_sma")) else None,
        "rsi": float(curr["rsi"]) if not pd.isna(curr.get("rsi")) else None,
        "rsi_sma": float(curr["rsi_sma"]) if not pd.isna(curr.get("rsi_sma")) else None,
        "macd_hist": float(curr["macd_hist"]) if not pd.isna(curr.get("macd_hist")) else None,
    }

    required = ["ema_fast", "ema_slow", "atr", "atr_sma", "rsi", "rsi_sma", "macd_hist"]
    for col in required:
        if pd.isna(curr.get(col)) or pd.isna(prev.get(col)):
            result["blocked_by"] = "nan_indicators"
            return result

    def fail(key):
        result["filters"][key] = False
        if result["blocked_by"] is None:
            result["blocked_by"] = key

    # 1. Trend (EMA20 > EMA50)
    result["filters"]["trend"] = curr["ema_fast"] > curr["ema_slow"]
    if not result["filters"]["trend"]:
        fail("trend")

    # 2. Price above EMA
    if cfg["close_above"] == "ema_fast":
        close_above_ok = curr["close"] > curr["ema_fast"]
    else:
        close_above_ok = curr["close"] > curr["ema_slow"]
    result["filters"]["close_above_ema"] = close_above_ok
    if not close_above_ok and result["blocked_by"] is None:
        fail("close_above_ema")

    # 3. ATR regime
    atr_ok = (not pd.isna(curr["atr_sma"])) and curr["atr"] > curr["atr_sma"]
    result["filters"]["atr_regime"] = atr_ok
    if not atr_ok and result["blocked_by"] is None:
        fail("atr_regime")

    # 4. RSI crossover + range
    rsi_cross = (curr["rsi"] > curr["rsi_sma"]) and (prev["rsi"] <= prev["rsi_sma"])
    rsi_in_range = cfg["rsi_min"] <= curr["rsi"] <= cfg["rsi_max"]
    rsi_ok = rsi_cross and rsi_in_range
    result["filters"]["rsi_crossover"] = rsi_ok
    if not rsi_ok and result["blocked_by"] is None:
        fail("rsi_crossover")

    # 5. MACD
    if cfg["macd_mode"] == "strict":
        macd_ok = curr["macd_hist"] > 0
    else:
        macd_ok = (curr["macd_hist"] > 0) or (curr["macd_line"] > curr["macd_signal_line"])
    result["filters"]["macd"] = macd_ok
    if not macd_ok and result["blocked_by"] is None:
        fail("macd")

    # 6. PMO
    if cfg.get("use_pmo", False):
        pmo_ok = (
            not pd.isna(curr.get("pmo"))
            and not pd.isna(curr.get("pmo_signal"))
            and curr["pmo"] > curr["pmo_signal"]
        )
        result["filters"]["pmo"] = pmo_ok
        if not pd.isna(curr.get("pmo")):
            result["values"]["pmo"] = float(curr["pmo"])
        if not pmo_ok and result["blocked_by"] is None:
            fail("pmo")

    # 7. Volume
    if cfg.get("use_volume_filter", False):
        threshold = cfg.get("volume_threshold", 0.8)
        vol_ok = (
            not pd.isna(curr.get("vol_sma"))
            and curr["volume"] > threshold * curr["vol_sma"]
        )
        result["filters"]["volume"] = vol_ok
        if not pd.isna(curr.get("vol_sma")):
            result["values"]["vol_ratio"] = float(curr["volume"] / curr["vol_sma"]) if curr["vol_sma"] else None
        if not vol_ok and result["blocked_by"] is None:
            fail("volume")

    # 8. MFI
    if cfg.get("use_mfi_filter", False):
        mfi_threshold = cfg.get("mfi_threshold", 50)
        mfi_ok = not pd.isna(curr.get("mfi")) and curr["mfi"] > mfi_threshold
        result["filters"]["mfi"] = mfi_ok
        if not pd.isna(curr.get("mfi")):
            result["values"]["mfi"] = float(curr["mfi"])
        if not mfi_ok and result["blocked_by"] is None:
            fail("mfi")

    # 9. ADX
    if cfg.get("use_adx_filter", False):
        adx_threshold = cfg.get("adx_threshold", 20)
        adx_ok = not pd.isna(curr.get("adx")) and curr["adx"] > adx_threshold
        result["filters"]["adx"] = adx_ok
        if not pd.isna(curr.get("adx")):
            result["values"]["adx"] = float(curr["adx"])
        if not adx_ok and result["blocked_by"] is None:
            fail("adx")

    # 10. BTC correlation filter
    if cfg.get("use_btc_filter", False):
        btc_ok = btc_close is not None and btc_ema is not None and btc_close > btc_ema
        result["filters"]["btc_filter"] = btc_ok
        if btc_close is not None:
            result["values"]["btc_close"] = float(btc_close)
        if btc_ema is not None:
            result["values"]["btc_ema"] = float(btc_ema)
        if not btc_ok and result["blocked_by"] is None:
            fail("btc_filter")

    # All filters passed?
    result["would_enter"] = result["blocked_by"] is None
    return result


def get_entry_reason(df: pd.DataFrame, cfg: dict) -> str:
    """Build a human-readable entry reason string."""
    curr = df.iloc[-2]
    parts = [
        f"EMA{cfg['ema_fast']} > EMA{cfg['ema_slow']}",
        f"ATR regime active",
        f"RSI crossover ({curr['rsi']:.1f})",
        f"MACD hist {'> 0' if cfg['macd_mode'] == 'strict' else 'bullish'}",
    ]
    if cfg.get("use_pmo"):
        parts.append("PMO > Signal")
    if cfg.get("use_volume_filter"):
        parts.append("Volume confirmed")
    if cfg.get("use_mfi_filter") and not pd.isna(curr.get("mfi")):
        parts.append(f"MFI {curr['mfi']:.1f}")
    if cfg.get("use_adx_filter") and not pd.isna(curr.get("adx")):
        parts.append(f"ADX {curr['adx']:.1f}")
    return " + ".join(parts)
