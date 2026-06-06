"""Phase F — ETH/BTC pair-trade signal helpers.

Mean reversion on the rolling z-score of the ETH/BTC price ratio.
Two-leg trade: long the relatively-cheap asset, short the relatively-rich
one. Net dollar-neutral by construction.

Entry: |z| >= entry_z (default 2.0)
Exit:  |z| <= exit_z   (default 0.5)
Stops: max_hold_bars OR z moves further against beyond atr_stop_mult
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

try:
    import numpy as np
    import pandas as pd
except ImportError:
    np = None
    pd = None

logger = logging.getLogger("crypto_bot.pair_signals")


# ─── Ratio + z-score ──────────────────────────────────────────────────────

def compute_ratio(eth_close, btc_close):
    """Return ETH/BTC close-price ratio as a Series. NaN where BTC is 0."""
    btc_safe = btc_close.where(btc_close != 0)  # avoid div-by-zero
    return eth_close / btc_safe


def rolling_z_score(series, window: int = 30):
    """Rolling z-score: (current - mean) / std over the trailing `window`.

    NaN for the first `window-1` bars (insufficient data). 0 when the
    rolling std is exactly zero (constant series — degenerate). Real
    finite values otherwise.
    """
    mean = series.rolling(window=window, min_periods=window).mean()
    std  = series.rolling(window=window, min_periods=window).std()
    z = (series - mean) / std
    # Treat std==0 as "no signal" (degenerate constant series). Where std
    # is NaN (insufficient data), let z stay NaN.
    return z.mask((std == 0), 0.0)


# ─── Entry ────────────────────────────────────────────────────────────────

def analyze_pair_entry(eth_close, btc_close, cfg: dict) -> dict:
    """Return entry decision with shape parallel to other bots' analyzers."""
    result = {
        "would_enter": False,
        "blocked_by":  None,
        "direction":   None,
        "z":           None,
        "ratio":       None,
    }
    window = cfg.get("z_window", 30)
    if eth_close is None or btc_close is None or len(eth_close) < window + 1:
        result["blocked_by"] = "insufficient_data"
        return result

    ratio = compute_ratio(eth_close, btc_close)
    z = rolling_z_score(ratio, window=window).iloc[-1]
    result["ratio"] = float(ratio.iloc[-1]) if not pd.isna(ratio.iloc[-1]) else None
    result["z"] = float(z) if not pd.isna(z) else None

    if pd.isna(z):
        result["blocked_by"] = "insufficient_data"
        return result

    entry_z = cfg.get("entry_z", 2.0)
    if z <= -entry_z:
        result["would_enter"] = True
        result["direction"] = "LONG_ETH_SHORT_BTC"
    elif z >= entry_z:
        result["would_enter"] = True
        result["direction"] = "SHORT_ETH_LONG_BTC"
    else:
        result["blocked_by"] = "within_band"
    return result


# ─── Exit ─────────────────────────────────────────────────────────────────

def check_pair_exit(
    eth_close, btc_close, position_direction: str,
    bars_held: int, entry_ratio: float, cfg: dict,
) -> Tuple[Optional[str], Optional[str]]:
    """Return (reason, kind) or (None, None)."""
    window = cfg.get("z_window", 30)
    if eth_close is None or btc_close is None or len(eth_close) < window + 1:
        return None, None

    ratio = compute_ratio(eth_close, btc_close)
    z = rolling_z_score(ratio, window=window).iloc[-1]
    if pd.isna(z):
        return None, None

    exit_z = cfg.get("exit_z", 0.5)
    entry_z = cfg.get("entry_z", 2.0)
    stop_z  = entry_z * cfg.get("atr_stop_mult", 2.0) / 2.0  # how much extra
    # Approximate stop: if currently |z| > entry_z + (entry_z / 2), we're 1.5x
    # past the entry threshold and the trade is failing.

    # 1. Reversion (best case — strategy worked)
    if abs(z) <= exit_z:
        return "Z Reverted", "full"

    # 2. Time stop (strategy aged out — preferred reporting over Z Stop
    # because Time Stop is a "didn't work" rather than "blew up", and
    # operators read these labels differently)
    if bars_held >= cfg.get("max_hold_bars", 5):
        return "Time Stop", "full"

    # 3. Adverse move beyond stop (strategy is actively failing)
    is_long_eth = position_direction == "LONG_ETH_SHORT_BTC"
    if is_long_eth and z <= -(entry_z * cfg.get("atr_stop_mult", 2.0)):
        return "Z Stop", "full"
    if (not is_long_eth) and z >= (entry_z * cfg.get("atr_stop_mult", 2.0)):
        return "Z Stop", "full"

    return None, None
