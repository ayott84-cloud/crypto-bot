"""Whale-tracking signal pipeline.

Polls the Hyperliquid whale tracker skill, aggregates positions across the
top-20 smart-money and bottom-20 rekt wallets, and classifies each coin into
one of: DIVERGENCE_LONG, DIVERGENCE_SHORT, CONSENSUS_LONG, CONSENSUS_SHORT, NONE.

Downstream: whale_main.py consumes the ranked list of signals and fires
trades via the shared executor.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Resolve whale_scanner from one of two places:
#   1. Vendored copy at crypto_bot/vendor/whale_scanner.py (production deploys)
#   2. ~/.claude/skills/hyperliquid-whale-tracker/scripts/ (local dev)
_VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
_HL_SKILL_DIR = Path.home() / ".claude" / "skills" / "hyperliquid-whale-tracker" / "scripts"
for _p in (_VENDOR_DIR, _HL_SKILL_DIR):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from whale_scanner import (  # type: ignore  # noqa: E402
    get_leaderboard,
    get_bulk_positions,
)

from whale_config import (
    WHALE_FETCH_COUNT,
    WHALE_RETRY_BACKOFF_SECONDS,
    MIN_SMART_TRADERS_PER_COIN,
    CONSENSUS_LONG_PCT,
    CONSENSUS_SHORT_PCT,
    DIVERGENCE_LONG_PCT,
    DIVERGENCE_SHORT_PCT,
    CROWDED_TRADE_PCT,
    REQUIRE_SMART_WINNING,
    HL_TO_WEEX_SYMBOL_OVERRIDES,
)

logger = logging.getLogger("crypto_bot.whale_signals")


# ─── Signal types ────────────────────────────────────────────────────────────

DIVERGENCE_LONG = "DIVERGENCE_LONG"
DIVERGENCE_SHORT = "DIVERGENCE_SHORT"
CONSENSUS_LONG = "CONSENSUS_LONG"
CONSENSUS_SHORT = "CONSENSUS_SHORT"
NONE = "NONE"


@dataclass
class CoinStats:
    """Aggregated stats for one coin across a cohort of wallets."""
    coin: str
    longs: int = 0
    shorts: int = 0
    long_notional: float = 0.0
    short_notional: float = 0.0
    upnl_sum: float = 0.0

    @property
    def total_traders(self) -> int:
        return self.longs + self.shorts

    @property
    def long_pct(self) -> float:
        return 100 * self.longs / self.total_traders if self.total_traders else 0.0

    @property
    def short_pct(self) -> float:
        return 100 * self.shorts / self.total_traders if self.total_traders else 0.0

    @property
    def net_notional(self) -> float:
        return self.long_notional - self.short_notional


@dataclass
class WhaleSignal:
    """A trade signal emitted for one coin."""
    coin: str                        # HL coin name, e.g. "BTC" or "kPEPE"
    weex_symbol: str                 # WEEX contract symbol, e.g. "BTCUSDT"
    signal: str                      # DIVERGENCE_LONG / CONSENSUS_LONG / etc
    direction: str                   # "LONG" or "SHORT"
    score: float                     # higher = stronger signal
    confidence: int                  # 1-10
    smart_long_pct: float
    smart_short_pct: float
    smart_n: int
    smart_net_notional: float
    smart_upnl_sum: float
    rekt_long_pct: float
    rekt_short_pct: float
    rekt_n: int
    reasoning: str = ""

    # ── Tier 1 confluence fields (populated by enrich_signal) ──────────────
    funding_rate: float = 0.0              # HL current funding (per-8h)
    funding_annual_pct: float = 0.0        # informational, for display
    liq_adverse_usd: float = 0.0           # $ of whale liqs at risk against us near entry
    liq_fuel_usd: float = 0.0              # $ of whale liqs that would squeeze in our favor
    liq_adverse_nearest_pct: Optional[float] = None  # distance to closest adverse liq
    recency_new_count: int = 0             # wallets that freshly entered since last cycle
    recency_growth_count: int = 0          # wallets that grew their position
    recency_exit_count: int = 0            # wallets that fully closed
    enrichment_applied: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Tier 1 confluence contexts ──────────────────────────────────────────────

@dataclass
class LiqContext:
    """Whale-liquidation cluster analysis for one coin + direction."""
    coin: str
    direction: str                        # "LONG" or "SHORT" — the direction we want to trade
    current_price: float
    adverse_notional_usd: float           # $ of whale liqs at risk AGAINST our trade within window
    fuel_notional_usd: float              # $ of whale liqs that squeeze IN our favor within window
    adverse_nearest_pct: Optional[float]  # distance (0..1) to closest adverse liq


@dataclass
class RecencyContext:
    """Position changes for one coin + direction since last poll cycle."""
    coin: str
    direction: str
    new_count: int = 0                    # wallets opened this coin-direction since last cycle
    growth_count: int = 0                 # wallets that grew their position in this direction
    shrink_count: int = 0                 # wallets that reduced their position
    exit_count: int = 0                   # wallets fully closed the position


# ─── Data fetch with retry ───────────────────────────────────────────────────

def _retry(fn, label: str, *args, **kwargs):
    """Call fn with exponential backoff retry on exception."""
    last_err = None
    for attempt, sleep_s in enumerate([0] + WHALE_RETRY_BACKOFF_SECONDS):
        if sleep_s:
            logger.info("Retry %d for %s in %ds", attempt, label, sleep_s)
            time.sleep(sleep_s)
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            logger.warning("%s failed on attempt %d: %s", label, attempt + 1, e)
    logger.error("%s failed after all retries: %s", label, last_err)
    raise last_err if last_err else RuntimeError(f"{label} failed")


def _parse_leaderboard_entry(entry: dict) -> dict:
    """Extract fields from a raw leaderboard row. Mirrors whale_scanner.parse_entry."""
    window_perfs = entry.get("windowPerformances", [])
    perfs = {}
    for item in window_perfs:
        if isinstance(item, list) and len(item) == 2:
            perfs[item[0]] = item[1]

    return {
        "address": entry.get("ethAddress", ""),
        "display_name": entry.get("displayName") or "Anonymous",
        "account_value": float(entry.get("accountValue", 0)),
        "pnl_alltime": float(perfs.get("allTime", {}).get("pnl", 0)),
        "pnl_month": float(perfs.get("month", {}).get("pnl", 0)),
    }


def fetch_cohorts(n: int = WHALE_FETCH_COUNT) -> Tuple[List[dict], List[dict]]:
    """Fetch top-N smart money wallets and top-N rekt wallets, with their positions.

    Returns (smart_wallets, rekt_wallets), each a list of dicts with:
        address, display_name, pnl_alltime, pnl_month, positions=[...]
    """
    logger.info("Fetching Hyperliquid leaderboard...")
    raw = _retry(get_leaderboard, "leaderboard")
    parsed = [_parse_leaderboard_entry(e) for e in raw if e.get("ethAddress")]

    # Smart money: sort by all-time PnL desc
    smart_sorted = sorted(parsed, key=lambda w: w["pnl_alltime"], reverse=True)
    # Rekt: sort by monthly PnL asc, keep only $1k+ account (avoid dust)
    rekt_candidates = [w for w in parsed if w["account_value"] >= 1000]
    rekt_sorted = sorted(rekt_candidates, key=lambda w: w["pnl_month"])

    # Scan more than N because some wallets may have no open positions (vaults, sub-accounts).
    # Target is to have N wallets with actual positions in each cohort.
    smart_pool = [w["address"] for w in smart_sorted[:n * 5]]
    rekt_pool = [w["address"] for w in rekt_sorted[:n * 5]]

    logger.info("Fetching positions for %d smart + %d rekt wallet candidates...",
                len(smart_pool), len(rekt_pool))

    smart_positions = _retry(get_bulk_positions, "smart_positions", smart_pool)
    rekt_positions = _retry(get_bulk_positions, "rekt_positions", rekt_pool)

    # Build address → leaderboard-meta map
    meta = {w["address"]: w for w in parsed}

    def _merge(position_results, count):
        out = []
        for r in position_results:
            if not r.get("has_positions"):
                continue
            addr = r.get("address")
            m = meta.get(addr, {})
            out.append({
                "address": addr,
                "display_name": m.get("display_name", "Anonymous"),
                "pnl_alltime": m.get("pnl_alltime", 0.0),
                "pnl_month": m.get("pnl_month", 0.0),
                "account_value": r.get("account_value", 0.0),
                "positions": r.get("positions", []),
            })
            if len(out) >= count:
                break
        return out

    smart = _merge(smart_positions, n)
    rekt = _merge(rekt_positions, n)

    logger.info("Cohorts ready: %d smart wallets, %d rekt wallets", len(smart), len(rekt))
    return smart, rekt


# ─── Aggregation ─────────────────────────────────────────────────────────────

def aggregate_cohort(wallets: List[dict]) -> Dict[str, CoinStats]:
    """Aggregate per-coin stats across a cohort of wallets."""
    stats: Dict[str, CoinStats] = defaultdict(lambda: CoinStats(coin=""))
    for w in wallets:
        for p in w.get("positions", []):
            coin = p.get("coin", "")
            if not coin:
                continue
            s = stats[coin]
            if not s.coin:
                s.coin = coin
            size = abs(float(p.get("size", 0)))
            entry = float(p.get("entry_price", 0))
            upnl = float(p.get("unrealized_pnl", 0))
            notional = size * entry
            if p.get("direction") == "LONG":
                s.longs += 1
                s.long_notional += notional
            elif p.get("direction") == "SHORT":
                s.shorts += 1
                s.short_notional += notional
            s.upnl_sum += upnl
    return dict(stats)


# ─── Classification ──────────────────────────────────────────────────────────

def hl_coin_to_weex_symbol(coin: str, weex_whitelist: Optional[set] = None,
                            check_top100: bool = True) -> Optional[str]:
    """Map Hyperliquid coin name → WEEX USDT perpetual symbol.

    Returns None if:
      - the coin is not listed on WEEX (when weex_whitelist is provided), OR
      - the coin is not in the top-N market cap list (when check_top100=True)

    check_top100=False bypasses the market-cap filter — used only in unit tests
    and the backtest (which has its own fixed universe). The live bot always
    passes the default True.
    """
    if coin in HL_TO_WEEX_SYMBOL_OVERRIDES:
        candidate = HL_TO_WEEX_SYMBOL_OVERRIDES[coin]
    else:
        candidate = f"{coin.upper()}USDT"

    if weex_whitelist is not None and candidate not in weex_whitelist:
        return None

    if check_top100:
        # Import lazily to avoid circular imports during whale_signals module load
        try:
            from whale_universe import is_top100
            if not is_top100(coin):
                return None
        except ImportError:
            logger.warning("whale_universe unavailable — skipping top-100 filter")

    return candidate


def classify(
    coin: str,
    smart: CoinStats,
    rekt: CoinStats,
    weex_symbol: str,
) -> Optional[WhaleSignal]:
    """Classify a coin's cohort stats into a signal (or None).

    Applies: min-trader floor, consensus/divergence thresholds, crowded-trade filter,
    edge-decay guard.
    """
    if smart.total_traders < MIN_SMART_TRADERS_PER_COIN:
        return None

    # Crowded trade guard: everyone agrees → fragile, skip
    if smart.long_pct >= CROWDED_TRADE_PCT and rekt.long_pct >= CROWDED_TRADE_PCT:
        logger.debug("%s: crowded LONG (smart %.0f%%, rekt %.0f%%) — skip",
                     coin, smart.long_pct, rekt.long_pct)
        return None
    if smart.short_pct >= CROWDED_TRADE_PCT and rekt.short_pct >= CROWDED_TRADE_PCT:
        logger.debug("%s: crowded SHORT (smart %.0f%%, rekt %.0f%%) — skip",
                     coin, smart.short_pct, rekt.short_pct)
        return None

    # Edge-decay guard: skip if smart money is currently losing on this coin
    if REQUIRE_SMART_WINNING and smart.upnl_sum < 0:
        logger.debug("%s: smart money underwater (uPnL $%.0f) — skip",
                     coin, smart.upnl_sum)
        return None

    # Classify
    signal_type = NONE
    direction = None
    if (smart.long_pct >= DIVERGENCE_LONG_PCT
            and rekt.short_pct >= DIVERGENCE_LONG_PCT
            and rekt.total_traders >= 3):
        signal_type, direction = DIVERGENCE_LONG, "LONG"
    elif (smart.short_pct >= DIVERGENCE_SHORT_PCT
          and rekt.long_pct >= DIVERGENCE_SHORT_PCT
          and rekt.total_traders >= 3):
        signal_type, direction = DIVERGENCE_SHORT, "SHORT"
    elif smart.long_pct >= CONSENSUS_LONG_PCT:
        signal_type, direction = CONSENSUS_LONG, "LONG"
    elif smart.short_pct >= CONSENSUS_SHORT_PCT:
        signal_type, direction = CONSENSUS_SHORT, "SHORT"

    if signal_type == NONE:
        return None

    dominant_pct = smart.long_pct if direction == "LONG" else smart.short_pct
    divergence_bonus = 25 if signal_type.startswith("DIVERGENCE") else 0
    score = (
        (dominant_pct - 50) * 2
        + abs(smart.net_notional) / 1e6
        + divergence_bonus
        + smart.upnl_sum / 1e5
    )
    confidence = max(1, min(10, round(score / 12)))

    reasoning_parts = [
        f"smart {direction.lower()} {dominant_pct:.0f}% ({smart.total_traders} wallets)",
        f"net ${smart.net_notional/1e6:+.1f}M",
        f"uPnL ${smart.upnl_sum:+,.0f}",
    ]
    if signal_type.startswith("DIVERGENCE"):
        opp_pct = rekt.short_pct if direction == "LONG" else rekt.long_pct
        reasoning_parts.append(f"rekt {'short' if direction=='LONG' else 'long'} {opp_pct:.0f}%")

    return WhaleSignal(
        coin=coin,
        weex_symbol=weex_symbol,
        signal=signal_type,
        direction=direction,
        score=score,
        confidence=confidence,
        smart_long_pct=smart.long_pct,
        smart_short_pct=smart.short_pct,
        smart_n=smart.total_traders,
        smart_net_notional=smart.net_notional,
        smart_upnl_sum=smart.upnl_sum,
        rekt_long_pct=rekt.long_pct,
        rekt_short_pct=rekt.short_pct,
        rekt_n=rekt.total_traders,
        reasoning=" · ".join(reasoning_parts),
    )


# ─── Tier 1: Liquidation cluster extraction ──────────────────────────────────

# Window: how far from current price we consider a liq cluster relevant.
# 6% is ~2× a typical 4H ATR for majors — captures realistic stop-hunt range.
LIQ_WINDOW_PCT = 0.06


def extract_liq_data(wallets: List[dict]) -> Dict[str, List[tuple]]:
    """Walk every position from a cohort and collect its liquidation data.

    Returns {coin: [(liq_price, notional_usd, direction), ...]}.
    Positions without a liquidation_price (cross-margin or zero-leverage) are
    skipped — they don't cluster meaningfully.
    """
    out: Dict[str, List[tuple]] = defaultdict(list)
    for w in wallets:
        for p in w.get("positions", []):
            coin = p.get("coin", "")
            liq = p.get("liquidation_price")
            if not coin or liq is None:
                continue
            try:
                liq_f = float(liq)
                size = abs(float(p.get("size", 0)))
                entry = float(p.get("entry_price", 0))
            except (TypeError, ValueError):
                continue
            if liq_f <= 0 or size <= 0 or entry <= 0:
                continue
            notional = size * entry
            out[coin].append((liq_f, notional, p.get("direction", "LONG")))
    return dict(out)


def compute_liq_context(
    coin: str,
    direction: str,
    current_price: float,
    smart_liq_data: Dict[str, List[tuple]],
    rekt_liq_data: Dict[str, List[tuple]],
) -> LiqContext:
    """Aggregate whale liqs near current price into adverse / fuel notional.

    For a LONG trade at current_price P:
      - Adverse = sum of LONG liq notional in [P*(1-W), P]
                  (if price drops to those liqs, longs cascade — hurts us).
      - Fuel    = sum of SHORT liq notional in [P, P*(1+W)]
                  (if price rises into those liqs, shorts cascade — helps us).
    For a SHORT trade, reverse: adverse = SHORT liqs above us (squeeze);
    fuel = LONG liqs below us (cascade accelerates our short).
    """
    W = LIQ_WINDOW_PCT
    low = current_price * (1 - W)
    high = current_price * (1 + W)

    adverse = 0.0
    fuel = 0.0
    adverse_nearest_abs_dist: Optional[float] = None

    # Combine smart + rekt liq data — both populations pose stop-hunt risk.
    all_liqs = (smart_liq_data.get(coin, []) +
                rekt_liq_data.get(coin, []))

    for liq_price, notional, liq_direction in all_liqs:
        if direction == "LONG":
            # Adverse: LONG liqs BELOW us (they trigger if price drops)
            if liq_direction == "LONG" and low <= liq_price < current_price:
                adverse += notional
                d = current_price - liq_price
                if adverse_nearest_abs_dist is None or d < adverse_nearest_abs_dist:
                    adverse_nearest_abs_dist = d
            # Fuel: SHORT liqs ABOVE us (they trigger if price rises)
            elif liq_direction == "SHORT" and current_price < liq_price <= high:
                fuel += notional
        else:  # SHORT
            # Adverse: SHORT liqs ABOVE us (squeeze risk)
            if liq_direction == "SHORT" and current_price < liq_price <= high:
                adverse += notional
                d = liq_price - current_price
                if adverse_nearest_abs_dist is None or d < adverse_nearest_abs_dist:
                    adverse_nearest_abs_dist = d
            # Fuel: LONG liqs BELOW us (accelerates our short)
            elif liq_direction == "LONG" and low <= liq_price < current_price:
                fuel += notional

    nearest_pct: Optional[float] = (
        adverse_nearest_abs_dist / current_price
        if adverse_nearest_abs_dist is not None and current_price > 0
        else None
    )
    return LiqContext(
        coin=coin,
        direction=direction,
        current_price=current_price,
        adverse_notional_usd=adverse,
        fuel_notional_usd=fuel,
        adverse_nearest_pct=nearest_pct,
    )


# ─── Tier 1: Recency (diff vs previous cycle) ────────────────────────────────

def build_position_snapshot(wallets: List[dict]) -> Dict[str, Dict[str, float]]:
    """Compact {wallet_address: {coin_direction: notional}} snapshot for diffing.

    Key format: "COIN_LONG" or "COIN_SHORT" so a single wallet holding both
    sides of a coin produces two entries.
    """
    snap: Dict[str, Dict[str, float]] = {}
    for w in wallets:
        addr = w.get("address", "")
        if not addr:
            continue
        entries: Dict[str, float] = {}
        for p in w.get("positions", []):
            coin = p.get("coin", "")
            direction = p.get("direction", "LONG")
            if not coin:
                continue
            try:
                size = abs(float(p.get("size", 0)))
                entry = float(p.get("entry_price", 0))
            except (TypeError, ValueError):
                continue
            notional = size * entry
            if notional <= 0:
                continue
            key = f"{coin}_{direction}"
            entries[key] = entries.get(key, 0.0) + notional
        snap[addr] = entries
    return snap


def compute_recency(
    coin: str,
    direction: str,
    prev: Dict[str, Dict[str, float]],
    curr: Dict[str, Dict[str, float]],
) -> RecencyContext:
    """Diff two position snapshots for one coin+direction. Returns per-wallet change counts.

    Threshold: a position change counts as 'growth' only if notional increased by
    ≥10% — filters out noise from tiny size adjustments or price-driven drift.
    """
    key = f"{coin}_{direction}"
    new_count = growth_count = shrink_count = exit_count = 0
    GROWTH_THRESHOLD = 0.10

    all_addrs = set(prev) | set(curr)
    for addr in all_addrs:
        p = prev.get(addr, {}).get(key, 0.0)
        c = curr.get(addr, {}).get(key, 0.0)
        if p == 0 and c > 0:
            new_count += 1
        elif p > 0 and c == 0:
            exit_count += 1
        elif p > 0 and c > 0:
            rel_change = (c - p) / p
            if rel_change >= GROWTH_THRESHOLD:
                growth_count += 1
            elif rel_change <= -GROWTH_THRESHOLD:
                shrink_count += 1
    return RecencyContext(
        coin=coin, direction=direction,
        new_count=new_count, growth_count=growth_count,
        shrink_count=shrink_count, exit_count=exit_count,
    )


# ─── Tier 1: Enrichment — adjust confidence/score from confluence ────────────

# Funding rate thresholds (per-8h rate).
# Typical funding ranges ±0.01% normal, ±0.05% elevated, ±0.1%+ extreme.
_FUNDING_CONFIRMING = 0.0001      # ≥|0.01%| per-8h is meaningfully directional
_FUNDING_CROWDED_EXTREME = 0.0005 # ≥|0.05%| per-8h = crowded one-sided

# Liquidation cluster sizing thresholds (relative to signal's own net notional).
_LIQ_ADVERSE_HEAVY_RATIO = 2.0    # adverse cluster > 2× signal conviction = real risk
_LIQ_ADVERSE_NEAR_PCT = 0.03      # within 3% of entry = can be hunted in a day
_LIQ_FUEL_HEAVY_RATIO = 2.0       # fuel cluster > 2× signal conviction = meaningful upside


def enrich_signal(
    sig: WhaleSignal,
    liq: Optional[LiqContext] = None,
    hl_ctx: Optional["HLContext"] = None,  # forward ref; imported lazily in callers
    recency: Optional[RecencyContext] = None,
) -> WhaleSignal:
    """Apply Tier 1 confluence adjustments to confidence/score. Returns a new signal.

    Rules (each independent, additive):
      - Funding confirms our direction → +1 conf, +10 score
      - Funding extreme against retail crowding → -1 conf, -10 score (crowded trade warning)
      - Adverse liq cluster > 2× conviction AND < 3% away → -2 conf, -25 score
      - Fuel liq cluster > 2× conviction → +1 conf, +10 score
      - ≥2 fresh whale entries this cycle → +1 conf, +10 score (new conviction)
      - ≥2 whales grew position → +1 conf, +5 score (DCA conviction)
      - ≥2 whales exited → -2 conf, -20 score (basket leaving)
    """
    delta_conf = 0
    delta_score = 0
    extras: List[str] = []

    # Funding confluence
    if hl_ctx is not None:
        sig.funding_rate = hl_ctx.funding_rate
        sig.funding_annual_pct = hl_ctx.funding_annualized_pct
        fr = hl_ctx.funding_rate
        if sig.direction == "LONG":
            if fr <= -_FUNDING_CONFIRMING:
                delta_conf += 1; delta_score += 10
                extras.append(f"funding {hl_ctx.funding_annualized_pct:+.1f}%/yr confirms")
            elif fr >= _FUNDING_CROWDED_EXTREME:
                delta_conf -= 1; delta_score -= 10
                extras.append(f"crowded long (funding +{hl_ctx.funding_annualized_pct:.1f}%/yr)")
        else:  # SHORT
            if fr >= _FUNDING_CONFIRMING:
                delta_conf += 1; delta_score += 10
                extras.append(f"funding +{hl_ctx.funding_annualized_pct:.1f}%/yr confirms")
            elif fr <= -_FUNDING_CROWDED_EXTREME:
                delta_conf -= 1; delta_score -= 10
                extras.append(f"crowded short (funding {hl_ctx.funding_annualized_pct:+.1f}%/yr)")

    # Liquidation cluster
    if liq is not None:
        sig.liq_adverse_usd = liq.adverse_notional_usd
        sig.liq_fuel_usd = liq.fuel_notional_usd
        sig.liq_adverse_nearest_pct = liq.adverse_nearest_pct
        own_notional = abs(sig.smart_net_notional)
        if own_notional > 0:
            if (liq.adverse_notional_usd >= _LIQ_ADVERSE_HEAVY_RATIO * own_notional
                    and liq.adverse_nearest_pct is not None
                    and liq.adverse_nearest_pct <= _LIQ_ADVERSE_NEAR_PCT):
                delta_conf -= 2; delta_score -= 25
                extras.append(f"adverse liq cluster ${liq.adverse_notional_usd/1e6:.1f}M "
                              f"at {liq.adverse_nearest_pct*100:.1f}%")
            if liq.fuel_notional_usd >= _LIQ_FUEL_HEAVY_RATIO * own_notional:
                delta_conf += 1; delta_score += 10
                extras.append(f"fuel ${liq.fuel_notional_usd/1e6:.1f}M in favor")

    # Recency
    if recency is not None:
        sig.recency_new_count = recency.new_count
        sig.recency_growth_count = recency.growth_count
        sig.recency_exit_count = recency.exit_count
        if recency.new_count >= 2:
            delta_conf += 1; delta_score += 10
            extras.append(f"{recency.new_count} fresh entries")
        if recency.growth_count >= 2:
            delta_conf += 1; delta_score += 5
            extras.append(f"{recency.growth_count} adding")
        if recency.exit_count >= 2:
            delta_conf -= 2; delta_score -= 20
            extras.append(f"{recency.exit_count} exiting")

    sig.confidence = max(1, min(10, sig.confidence + delta_conf))
    sig.score = sig.score + delta_score
    if extras:
        sig.reasoning = sig.reasoning + " · " + " · ".join(extras)
    sig.enrichment_applied = True
    return sig


# ─── End-to-end signal generation ────────────────────────────────────────────

def generate_signals(weex_whitelist: Optional[set] = None) -> List[WhaleSignal]:
    """Fetch, aggregate, classify — returns signals ranked by score desc.

    weex_whitelist: set of WEEX symbols we can actually trade. Non-listed
    coins are dropped. Pass None to include everything (testing only).
    """
    smart_wallets, rekt_wallets = fetch_cohorts()
    smart_stats = aggregate_cohort(smart_wallets)
    rekt_stats = aggregate_cohort(rekt_wallets)

    # Union of all coins in either cohort
    all_coins = set(smart_stats.keys()) | set(rekt_stats.keys())

    signals: List[WhaleSignal] = []
    for coin in all_coins:
        weex_sym = hl_coin_to_weex_symbol(coin, weex_whitelist)
        if weex_sym is None:
            continue
        smart = smart_stats.get(coin, CoinStats(coin=coin))
        rekt = rekt_stats.get(coin, CoinStats(coin=coin))
        sig = classify(coin, smart, rekt, weex_sym)
        if sig:
            signals.append(sig)

    signals.sort(key=lambda s: s.score, reverse=True)
    logger.info("Generated %d whale signals (after WEEX filter + classify)",
                len(signals))
    for s in signals[:10]:
        logger.info("  [%s] %s %s conf=%d/10 score=%.1f — %s",
                    s.signal, s.coin, s.direction, s.confidence, s.score, s.reasoning)
    return signals


# ─── Signal-flip evaluation (for exit logic) ─────────────────────────────────

def compute_dominant_pct(coin: str, direction: str,
                          smart_stats: Dict[str, CoinStats]) -> Optional[float]:
    """For an open position, return the current smart-money consensus % in its direction.

    Used by whale_main.py to decide whether to exit early on signal flip.
    Returns None if the coin has no smart-money positions at all in this scan.
    """
    s = smart_stats.get(coin)
    if s is None or s.total_traders == 0:
        return None
    return s.long_pct if direction == "LONG" else s.short_pct
