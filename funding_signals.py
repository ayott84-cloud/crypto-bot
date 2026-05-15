"""Funding-fade signal pipeline.

Per-symbol classifier that turns current funding rate + 30-day rolling
distribution into an actionable trade signal (LONG / SHORT / None).

Signal logic
------------
For each coin in (WEEX-listed ∩ top-100 market cap):
  1. Fetch 30 days of funding history (via funding_history.py, HL backed).
  2. Read current funding rate from HL metaAndAssetCtxs (already cached
     per cycle by whale_hl_data.py — we just import & reuse).
  3. Classify:
       extreme = is_extreme(current, distribution, pct=97, floor=0.0005)
       - "top"    → CROWDED_LONG_FADE → SHORT signal
       - "bottom" → CROWDED_SHORT_FADE → LONG signal
       - None    → skip
  4. Gate by filters:
       - Min OI USD (cheap to compute from HL ctx: oi × mark_price)
       - ATR regime (FLIPPED: require ATR < ATR_SMA for fade strategy)
       - Trend filter (skip SHORT fade if price > EMA20 with positive slope;
         mirror for LONG)
       - Per-coin cooldown
  5. Emit FundingSignal dataclass with all the diagnostics.

Independent of whale signal classifier — different thesis, different data,
different sizing. Shares only top-100 universe and ATR-from-klines plumbing.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("crypto_bot.funding_signals")


# ─── Signal types ────────────────────────────────────────────────────────────

CROWDED_LONG_FADE = "CROWDED_LONG_FADE"     # positive funding extreme → SHORT
CROWDED_SHORT_FADE = "CROWDED_SHORT_FADE"   # negative funding extreme → LONG
NONE = "NONE"


@dataclass
class FundingSignal:
    """A funding-fade trade signal."""
    coin: str                    # HL/WEEX coin name (e.g. "BTC")
    weex_symbol: str             # WEEX contract symbol (e.g. "BTCUSDT")
    signal: str                  # CROWDED_LONG_FADE / CROWDED_SHORT_FADE / NONE
    direction: str               # "LONG" or "SHORT" (the trade direction, opposite of crowd)
    current_funding: float       # per-8h rate
    current_funding_annual_pct: float
    percentile: float            # rank of current within 30d distribution
    history_n: int               # number of data points in the rolling history
    # Filter diagnostics
    oi_usd: float = 0.0
    atr: float = 0.0
    atr_sma: float = 0.0
    atr_below_sma: bool = False  # True = low-vol regime, OK for fade
    trend_ok: bool = False       # True = trend doesn't oppose the fade
    confidence: int = 5          # 1-10, scored from percentile + abs magnitude
    reasoning: str = ""
    classified_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Classifier ──────────────────────────────────────────────────────────────

def classify(
    coin: str,
    weex_symbol: str,
    current_funding: float,
    history: List[float],
    *,
    oi_usd: float = 0.0,
    atr: float = 0.0,
    atr_sma: float = 0.0,
    trend_ok: bool = True,
    cfg: Optional[dict] = None,
) -> Optional[FundingSignal]:
    """Build a FundingSignal (or None) for one coin at one moment.

    `cfg`: lets tests override thresholds. None → use module-level config.
    Caller pre-checks cooldown, universe filter, signal-log dedup.
    Returns None if signal doesn't classify OR a filter vetoes it.
    """
    from funding_history import is_extreme, percentile_of
    if cfg is None:
        from funding_config import (
            FUNDING_PERCENTILE_THRESHOLD,
            FUNDING_ABSOLUTE_FLOOR,
            FUNDING_MIN_HISTORY_POINTS,
            FUNDING_MIN_OI_USD,
            FUNDING_REQUIRE_LOW_VOL,
            FUNDING_USE_TREND_FILTER,
        )
        cfg = {
            "pct": FUNDING_PERCENTILE_THRESHOLD,
            "floor": FUNDING_ABSOLUTE_FLOOR,
            "min_hist": FUNDING_MIN_HISTORY_POINTS,
            "min_oi": FUNDING_MIN_OI_USD,
            "require_low_vol": FUNDING_REQUIRE_LOW_VOL,
            "use_trend": FUNDING_USE_TREND_FILTER,
        }

    if len(history) < cfg["min_hist"]:
        logger.debug("%s: only %d history points (need %d) — skip",
                     coin, len(history), cfg["min_hist"])
        return None

    extreme = is_extreme(current_funding, history,
                         percentile_threshold=cfg["pct"],
                         absolute_floor=cfg["floor"])
    if extreme is None:
        return None

    direction = "SHORT" if extreme == "top" else "LONG"
    signal_type = CROWDED_LONG_FADE if extreme == "top" else CROWDED_SHORT_FADE

    # Filter gates
    if cfg["min_oi"] > 0 and oi_usd < cfg["min_oi"]:
        logger.debug("%s: OI $%.0fM < min $%.0fM — skip", coin,
                     oi_usd / 1e6, cfg["min_oi"] / 1e6)
        return None

    atr_below_sma = atr > 0 and atr_sma > 0 and atr < atr_sma
    if cfg["require_low_vol"] and not atr_below_sma:
        logger.debug("%s: ATR %.4f >= ATR_SMA %.4f — high-vol regime, skip fade",
                     coin, atr, atr_sma)
        return None

    if cfg["use_trend"] and not trend_ok:
        logger.debug("%s: trend opposes %s fade — skip", coin, direction)
        return None

    # Confidence: percentile_rank (50-100 → 5-10) + funding magnitude bonus
    pct = percentile_of(current_funding, history) or 50.0
    base_conf = int(round((max(pct, 100 - pct) - 50) / 10))  # 0..5
    mag_bonus = 0
    if abs(current_funding) >= cfg["floor"] * 2:  # 2x floor = very extreme
        mag_bonus += 2
    elif abs(current_funding) >= cfg["floor"] * 1.5:
        mag_bonus += 1
    confidence = max(1, min(10, 5 + base_conf + mag_bonus - 5))

    annual_pct = current_funding * 3 * 365 * 100

    reasoning_parts = [
        f"funding {annual_pct:+.1f}%/yr (pct {pct:.0f})",
        f"OI ${oi_usd / 1e6:.0f}M",
    ]
    if atr_below_sma:
        reasoning_parts.append("low-vol regime")
    if trend_ok:
        reasoning_parts.append("trend not opposed")

    return FundingSignal(
        coin=coin,
        weex_symbol=weex_symbol,
        signal=signal_type,
        direction=direction,
        current_funding=current_funding,
        current_funding_annual_pct=annual_pct,
        percentile=pct,
        history_n=len(history),
        oi_usd=oi_usd,
        atr=atr,
        atr_sma=atr_sma,
        atr_below_sma=atr_below_sma,
        trend_ok=trend_ok,
        confidence=confidence,
        reasoning=" · ".join(reasoning_parts),
    )


# ─── Helpers used by funding_main ────────────────────────────────────────────

def compute_atr_and_sma(klines: List, period: int = 14, sma_period: int = 20):
    """Wilder's ATR + simple SMA of ATR. Stdlib-only (mirrors whale_main.compute_atr)."""
    if not klines or len(klines) < period + sma_period:
        return None, None
    try:
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        closes = [float(k[4]) for k in klines]
    except (ValueError, IndexError, TypeError):
        return None, None

    n = len(klines)
    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return None, None
    atr = sum(trs[:period]) / period
    atr_series = [None] * (period - 1) + [atr]
    for i in range(period, n):
        atr = (atr * (period - 1) + trs[i]) / period
        atr_series.append(atr)

    if not atr_series or atr_series[-1] is None:
        return None, None

    last_atr = atr_series[-1]
    # SMA of last sma_period ATR values
    valid = [a for a in atr_series[-sma_period:] if a is not None]
    if len(valid) < sma_period:
        return last_atr, None
    atr_sma = sum(valid) / len(valid)
    return last_atr, atr_sma


def compute_ema_and_slope(klines: List, period: int = 20):
    """Return (ema_now, ema_slope_sign). Slope sign = +1 / 0 / -1."""
    if not klines or len(klines) < period + 5:
        return None, 0
    try:
        closes = [float(k[4]) for k in klines]
    except (ValueError, IndexError, TypeError):
        return None, 0
    # Simple EMA
    alpha = 2 / (period + 1)
    ema = sum(closes[:period]) / period  # seed
    series = [None] * (period - 1) + [ema]
    for c in closes[period:]:
        ema = alpha * c + (1 - alpha) * ema
        series.append(ema)
    if len(series) < 5:
        return series[-1], 0
    recent = [s for s in series[-5:] if s is not None]
    if len(recent) < 5:
        return series[-1], 0
    slope_sign = 1 if recent[-1] > recent[0] else (-1 if recent[-1] < recent[0] else 0)
    return series[-1], slope_sign


def trend_allows_fade(direction: str, last_close: float, ema: float, slope_sign: int) -> bool:
    """For a SHORT fade (positive-funding crowded long), don't enter if price
    is solidly above EMA20 AND rising. Mirror for LONG fade.

    Returns True if the trend doesn't strongly oppose the fade.
    """
    if ema is None or last_close <= 0:
        return True  # missing data → don't block
    above_ema = last_close > ema
    if direction == "SHORT":
        # Bad: above EMA AND slope up. Skip.
        return not (above_ema and slope_sign > 0)
    else:  # LONG
        # Bad: below EMA AND slope down.
        return not (not above_ema and slope_sign < 0)


# ─── Settlement window ───────────────────────────────────────────────────────

def in_execution_window(now: Optional[datetime] = None, *, window_minutes: int = 30,
                        fixing_hours_utc=(0, 8, 16)) -> bool:
    """True if `now` (UTC) is within ±window_minutes of any funding fixing.

    Defaults to 30-min half-window → 60-min total window centered on
    00:00 / 08:00 / 16:00 UTC. Outside this window, the bot doesn't open
    new positions (peer-review: funding extremes between fixings can
    persist for days without reversion).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    for h in fixing_hours_utc:
        fixing = now.replace(hour=h, minute=0, second=0, microsecond=0)
        diff_min = abs((now - fixing).total_seconds()) / 60
        if diff_min <= window_minutes:
            return True
        # Also check the fixing one cycle ahead (e.g. 23:50 is in window for 00:00 next day)
        for delta in (-1, 1):
            shifted = fixing.replace() if delta == 0 else None
        # Cross-day check: 00:00 UTC is 24:00 of previous day
    # Cross-midnight handling (T-30 from 00:00 = 23:30 prev day)
    for h in fixing_hours_utc:
        if h == 0:
            yesterday_2400 = now.replace(hour=23, minute=30, second=0, microsecond=0)
            if now >= yesterday_2400 and (now.hour, now.minute) >= (23, 30):
                return True
    return False
