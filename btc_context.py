"""BTC correlation-filter context: per-asset EMA cache.

Phase B.1 of the comprehensive enhancement plan.

The momentum bot's BTC correlation filter (`use_btc_filter` in each ASSETS
entry) needs BTC close + EMA on each asset's timeframe + each asset's
configured `btc_ema_period`. The previous implementation in main.py
hardcoded period=50, silently ignoring per-asset configs (ADA_4H=100,
DOT_1D=200, SHIB_1D=100).

The fix lives here, split into two helpers:

  `_btc_context_keys(assets) -> set[(interval, ema_period)]`
      Pure function — what combos do we need? Testable without pandas.

  `_build_btc_context(executor, assets, compute_ema=None) -> dict`
      Thin wrapper that fetches klines + computes per-combo EMA. The
      `compute_ema` callable is injectable so unit tests don't require
      pandas_ta in the test environment.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger("crypto_bot.btc_context")

_BTC_SYMBOL = "BTCUSDT"
_BTC_KLINES_LIMIT = 200
_MIN_BARS_FOR_EMA = 60  # below this, return (None, None) — caller decides what to do


# ─── Pure key set ──────────────────────────────────────────────────────────

def _btc_context_keys(assets: dict) -> set[tuple[str, int]]:
    """Return the set of (interval, ema_period) combinations the BTC filter needs.

    Any asset with `use_btc_filter=True` contributes one combination. Missing
    `btc_ema_period` defaults to 50 for backward compatibility with pre-B.1
    behavior.
    """
    return {
        (cfg["interval"], cfg.get("btc_ema_period", 50))
        for cfg in assets.values()
        if cfg.get("use_btc_filter", False)
    }


# ─── pandas_ta-backed default EMA computer ─────────────────────────────────

def _default_compute_ema(klines: list, period: int) -> tuple[float, float]:
    """Default (last_close, ema) using pandas_ta on the close series.

    Lazy-imports pandas_ta so this module stays import-light for tests that
    inject their own `compute_ema`.
    """
    # Lazy imports — only when actually used in production.
    import pandas_ta as _ta
    from signals import build_dataframe

    df = build_dataframe(klines)
    last_close = float(df.iloc[-2]["close"])
    ema_val = float(_ta.ema(df["close"], length=period).iloc[-2])
    return last_close, ema_val


# ─── Builder with one entry per (interval, ema_period) combo ───────────────

def _build_btc_context(
    executor,
    assets: dict,
    compute_ema: Optional[Callable[[list, int], tuple[float, float]]] = None,
) -> dict:
    """Build a {(interval, ema_period): (close, ema)} cache the entry loop can read.

    The lookup site (main.py inside the per-asset loop) keys by
    `(cfg["interval"], cfg.get("btc_ema_period", 50))` to retrieve the
    BTC context matching that asset's filter.

    For each unique (interval, ema_period) combination across all assets
    with `use_btc_filter=True`, fetches BTC klines for the interval once
    and computes the EMA at the requested period. On insufficient data or
    fetch errors, the entry is `(None, None)` and the caller treats the
    filter as "data missing — fail closed."

    The `compute_ema` callable is injectable so unit tests don't require
    pandas_ta installed.
    """
    if compute_ema is None:
        compute_ema = _default_compute_ema

    ctx: dict[tuple[str, int], tuple[Optional[float], Optional[float]]] = {}
    for tf, period in _btc_context_keys(assets):
        try:
            klines = executor.get_klines(_BTC_SYMBOL, tf, _BTC_KLINES_LIMIT)
        except Exception as e:
            logger.error("Failed to fetch BTC %s for correlation filter: %s", tf, e)
            ctx[(tf, period)] = (None, None)
            continue

        if not klines or len(klines) < _MIN_BARS_FOR_EMA:
            logger.warning(
                "Insufficient BTC %s klines for correlation filter (%d bars)",
                tf, len(klines) if klines else 0,
            )
            ctx[(tf, period)] = (None, None)
            continue

        try:
            close, ema_val = compute_ema(klines, period)
            ctx[(tf, period)] = (close, ema_val)
            logger.debug(
                "BTC %s context: close=%.2f ema%d=%.2f bullish=%s",
                tf, close, period, ema_val, close > ema_val,
            )
        except Exception as e:
            logger.error("EMA computation failed for BTC %s period=%d: %s",
                         tf, period, e)
            ctx[(tf, period)] = (None, None)

    return ctx
