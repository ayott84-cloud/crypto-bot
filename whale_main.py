"""Whale-Tracking Bot — Main Loop.

Polls Hyperliquid for smart-money vs rekt-money positioning, classifies
signals, and fires LONG/SHORT trades on WEEX. Runs alongside main.py
(bot 1) — they share the WEEX account, state.json, journal, dashboard,
and a global 8-position cap on a first-come-first-served basis.

Usage:
    python whale_main.py
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

# Ensure bot dir is on path
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

from config import (
    DRY_RUN, TRADING_ENABLED, LOG_FILE, MAX_POSITIONS,
    JOURNAL_FILE,
)
from whale_config import (
    WHALE_PAUSED,
    WHALE_POLL_INTERVAL_SECONDS,
    WHALE_MARGIN_CONSENSUS, WHALE_MARGIN_DIVERGENCE, WHALE_LEVERAGE,
    WHALE_ATR_PERIOD, WHALE_ATR_INTERVAL,
    WHALE_SL_ATR_MULT, WHALE_TP_ATR_MULT,
    WHALE_MAX_7D_LOSS_USD, WHALE_COOLDOWN_HOURS,
    SIGNAL_FLIP_THRESHOLD,
    WHALE_STATE_KEY_PREFIX, WHALE_STRATEGY_TAG,
    WHALE_SYMBOL_WHITELIST_CACHE, WHALE_SYMBOL_CACHE_TTL_HOURS,
    WHALE_SIGNAL_LOG,
)
from executor import Executor
from position_manager import (
    load_state, save_state, get_open_positions,
    register_entry, register_exit, can_open_new_position,
)
from journal import log_trade
from whale_signals import (
    generate_signals, aggregate_cohort, fetch_cohorts,
    compute_dominant_pct, hl_coin_to_weex_symbol,
    extract_liq_data, compute_liq_context,
    build_position_snapshot, compute_recency,
    enrich_signal, classify, CoinStats,
    DIVERGENCE_LONG, DIVERGENCE_SHORT, CONSENSUS_LONG, CONSENSUS_SHORT,
)
from whale_hl_data import fetch_meta_and_ctxs

try:
    from notifier import notify_trade_opened, notify_trade_closed
except ImportError:
    notify_trade_opened = None
    notify_trade_closed = None

try:
    from dashboard import build_dashboard
except ImportError:
    build_dashboard = None

logger = logging.getLogger("whale_bot")

VERSION = "1.0.0"


# ─── Logging setup ───────────────────────────────────────────────────────────

def setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    try:
        fh = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except IOError as e:
        print(f"Warning: could not open log file: {e}")


def print_banner():
    print()
    print("=" * 60)
    print(f"  WHALE-TRACKING BOT v{VERSION}")
    print("=" * 60)
    print(f"  Mode:              {'DRY RUN' if DRY_RUN else 'LIVE TRADING'}")
    print(f"  Trading enabled:   {TRADING_ENABLED}")
    print(f"  Whale paused:      {WHALE_PAUSED}  {'(no new entries)' if WHALE_PAUSED else ''}")
    print(f"  Poll interval:     {WHALE_POLL_INTERVAL_SECONDS}s ({WHALE_POLL_INTERVAL_SECONDS // 60}m)")
    print(f"  Consensus margin:  ${WHALE_MARGIN_CONSENSUS} x {WHALE_LEVERAGE}x = ${WHALE_MARGIN_CONSENSUS * WHALE_LEVERAGE} notional")
    print(f"  Divergence margin: ${WHALE_MARGIN_DIVERGENCE} x {WHALE_LEVERAGE}x = ${WHALE_MARGIN_DIVERGENCE * WHALE_LEVERAGE} notional")
    print(f"  Max positions (shared with bot 1): {MAX_POSITIONS}")
    print("=" * 60)
    print()


# ─── WEEX symbol whitelist (cached) ──────────────────────────────────────────

_WEEX_FALLBACK = {
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT",
    "AVAXUSDT", "LTCUSDT", "ADAUSDT", "DOTUSDT", "BNBUSDT", "NEARUSDT",
    "AAVEUSDT", "SUIUSDT", "TRBUSDT", "APTUSDT", "ZROUSDT", "TAOUSDT",
    "FILUSDT", "ARBUSDT", "OPUSDT", "SHIBUSDT", "PEPEUSDT", "WLFIUSDT",
    "HYPEUSDT", "LITUSDT", "ZECUSDT", "TRXUSDT", "ATOMUSDT", "UNIUSDT",
    "ETCUSDT", "ICPUSDT", "FETUSDT", "ALGOUSDT", "INJUSDT", "RNDRUSDT",
    "ENSUSDT", "MNTUSDT", "TONUSDT", "BONKUSDT", "MATICUSDT",
}


def load_weex_whitelist(executor: Executor) -> set:
    """Load the set of WEEX USDT perpetual symbols, using disk cache if fresh.

    Falls back to a hardcoded common set if the cache is empty or the API
    call returns 0 symbols (which happens in DRY_RUN without WEEX creds).
    """
    cache = WHALE_SYMBOL_WHITELIST_CACHE
    if cache.exists():
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(payload["fetched_at"])
            age_hours = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600
            cached_syms = set(payload.get("symbols", []))
            if age_hours < WHALE_SYMBOL_CACHE_TTL_HOURS and len(cached_syms) >= 10:
                logger.info("Loaded WEEX whitelist from cache (%d symbols, %.1fh old)",
                            len(cached_syms), age_hours)
                return cached_syms
            if len(cached_syms) < 10:
                logger.warning("Cached WEEX whitelist has only %d symbols — ignoring, refetching",
                               len(cached_syms))
        except Exception as e:
            logger.warning("WEEX whitelist cache unreadable, refetching: %s", e)

    logger.info("Fetching WEEX contract list...")
    try:
        contracts = executor.get_contract_info()
        symbols = {c.get("symbol", "") for c in contracts if c.get("symbol", "").endswith("USDT")}
        symbols.discard("")
        if len(symbols) >= 10:
            cache.write_text(json.dumps({
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "symbols": sorted(symbols),
            }, indent=2), encoding="utf-8")
            logger.info("Cached %d WEEX USDT symbols to %s", len(symbols), cache.name)
            return symbols
        logger.warning("WEEX returned only %d symbols (no creds? DRY_RUN?) — using fallback",
                       len(symbols))
    except Exception as e:
        logger.error("Failed to fetch WEEX contracts: %s — using fallback", e)

    return set(_WEEX_FALLBACK)


# ─── ATR calculation for SL/TP ───────────────────────────────────────────────

def compute_atr(klines: List, period: int = 14) -> Optional[float]:
    """Compute Wilder's ATR from WEEX klines. Stdlib-only (no pandas dependency).

    WEEX kline format: [timestamp, open, high, low, close, volume, ...]
    Returns None if insufficient data.
    """
    if not klines or len(klines) < period + 1:
        return None
    try:
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        closes = [float(k[4]) for k in klines]
    except (ValueError, IndexError, TypeError):
        return None

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
        return None
    # Wilder's smoothing: seed = simple average of first `period` TRs,
    # then ATR_i = (ATR_{i-1}*(period-1) + TR_i) / period.
    atr = sum(trs[:period]) / period
    for i in range(period, n):
        atr = (atr * (period - 1) + trs[i]) / period
    return None if math.isnan(atr) else float(atr)


def compute_trade_levels(entry_price: float, atr: float, direction: str) -> Dict[str, float]:
    """Calculate SL and TP levels from entry + ATR."""
    sl_dist = WHALE_SL_ATR_MULT * atr
    tp_dist = WHALE_TP_ATR_MULT * atr
    if direction == "LONG":
        return {
            "sl": entry_price - sl_dist,
            "tp": entry_price + tp_dist,
        }
    else:  # SHORT
        return {
            "sl": entry_price + sl_dist,
            "tp": entry_price - tp_dist,
        }


# ─── Position sizing ─────────────────────────────────────────────────────────

def calc_quantity(symbol: str, price: float, margin_usd: float,
                   executor: Executor) -> str:
    """Compute quantity respecting WEEX step/min, using a custom margin amount."""
    notional = margin_usd * WHALE_LEVERAGE
    raw_qty = notional / price
    step = executor.get_qty_step(symbol)
    min_qty = executor.get_min_qty(symbol)
    if step > 0:
        qty = math.floor(raw_qty / step) * step
    else:
        qty = raw_qty
    if qty < min_qty:
        qty = min_qty
    decimals = max(0, -int(math.log10(step))) if 0 < step < 1 else 3
    return f"{qty:.{decimals}f}"


# ─── Cooldown tracking ───────────────────────────────────────────────────────

def _cooldown_map(state: dict) -> dict:
    """Return (and init if missing) the whale-cooldown sub-state."""
    return state.setdefault("whale_cooldowns", {})


def is_on_cooldown(state: dict, coin: str) -> bool:
    cd = _cooldown_map(state)
    ts = cd.get(coin)
    if not ts:
        return False
    try:
        exited = datetime.fromisoformat(ts)
    except ValueError:
        return False
    hours_since = (datetime.now(timezone.utc) - exited).total_seconds() / 3600
    return hours_since < WHALE_COOLDOWN_HOURS


def record_cooldown(state: dict, coin: str) -> None:
    _cooldown_map(state)[coin] = datetime.now(timezone.utc).isoformat()


# ─── 7-day loss guard ────────────────────────────────────────────────────────

def recent_whale_pnl(days: int = 7) -> float:
    """Sum realized PnL across whale trades closed in the last N days.

    Reads the JSONL journal. Returns 0 if journal missing or no trades.
    """
    try:
        from journal import read_trades
        cutoff = datetime.now() - timedelta(days=days)
        total = 0.0
        for t in read_trades(max_rows=10000):
            strategy = t.get("strategy", "")
            if not isinstance(strategy, str) or not strategy.startswith(WHALE_STRATEGY_TAG):
                continue
            date_closed_str = t.get("date_closed")
            if not date_closed_str:
                continue
            try:
                date_closed = datetime.fromisoformat(date_closed_str)
            except (TypeError, ValueError):
                continue
            # Drop tz-info for naive comparison with cutoff
            if date_closed.tzinfo is not None:
                date_closed = date_closed.replace(tzinfo=None)
            if date_closed < cutoff:
                continue
            net = t.get("net_pnl", 0)
            if isinstance(net, (int, float)):
                total += float(net)
        return total
    except Exception as e:
        logger.warning("Could not read recent whale PnL from journal: %s", e)
        return 0.0


# ─── Entry / exit actions ────────────────────────────────────────────────────

def _state_key(coin: str) -> str:
    return f"{WHALE_STATE_KEY_PREFIX}{coin.upper()}"


def open_whale_position(
    executor: Executor,
    state: dict,
    signal,
    atr: float,
) -> bool:
    """Open a whale trade. Returns True if position opened."""
    key = _state_key(signal.coin)
    if key in state.get("positions", {}):
        logger.info("%s already open, skipping re-entry", key)
        return False
    if not can_open_new_position(state):
        logger.info("Global 8-slot cap reached (%d open), cannot open %s",
                    len(state.get("positions", {})), key)
        return False

    price = executor.get_symbol_price(signal.weex_symbol)
    if not price or price <= 0:
        logger.warning("%s: could not fetch price from WEEX, skipping",
                       signal.weex_symbol)
        return False

    margin = WHALE_MARGIN_DIVERGENCE if signal.signal.startswith("DIVERGENCE") else WHALE_MARGIN_CONSENSUS
    qty = calc_quantity(signal.weex_symbol, price, margin, executor)
    levels = compute_trade_levels(price, atr, signal.direction)

    tick = executor.get_tick_size(signal.weex_symbol)
    decimals = max(0, -int(math.log10(tick))) if 0 < tick < 1 else 2
    sl_str = f"{levels['sl']:.{decimals}f}"
    tp_str = f"{levels['tp']:.{decimals}f}"

    logger.info("OPENING %s %s @ %.*f qty=%s SL=%s TP=%s (margin=$%.0f, %s conf=%d)",
                signal.direction, signal.weex_symbol, decimals, price, qty,
                sl_str, tp_str, margin, signal.signal, signal.confidence)

    # Place the market order (includes attached SL)
    if signal.direction == "LONG":
        result = executor.open_long(signal.weex_symbol, qty, sl_trigger_price=sl_str)
    else:
        result = executor.open_short(signal.weex_symbol, qty, sl_trigger_price=sl_str)

    if not result.get("ok"):
        logger.error("Open order failed for %s: %s", signal.weex_symbol, result)
        return False

    # Place TP conditional order (separate from attached SL)
    tp_result = executor.place_tp_order(signal.weex_symbol, signal.direction, tp_str, qty)
    if not tp_result.get("ok"):
        logger.warning("TP order failed for %s (position still opened): %s",
                       signal.weex_symbol, tp_result)

    # Register in shared state (use prefix-tagged key so bot 1 won't touch it)
    strategy_name = f"{WHALE_STRATEGY_TAG} {signal.coin} {signal.direction}"
    reason = (f"{signal.signal} | smart {signal.direction.lower()} "
              f"{(signal.smart_long_pct if signal.direction=='LONG' else signal.smart_short_pct):.0f}% "
              f"({signal.smart_n} wallets) | conf {signal.confidence}/10 | {signal.reasoning}")

    register_entry(
        state,
        state_key=key,
        entry_price=price,
        atr_at_entry=atr,
        quantity=qty,
        strategy=strategy_name,
        entry_reason=reason,
        symbol=signal.weex_symbol,
    )
    # Persist whale-specific fields inside the position dict.
    pos = state["positions"][key]
    pos["direction"] = signal.direction
    pos["sl"] = levels["sl"]
    pos["tp"] = levels["tp"]
    pos["signal_type"] = signal.signal
    pos["confidence"] = signal.confidence
    pos["margin_usd"] = margin
    save_state(state, owner="whale")

    # Log to Excel journal (entry row; exit filled in on close)
    log_trade(
        symbol=signal.weex_symbol,
        direction=signal.direction,
        entry_price=price,
        exit_price=None,
        quantity=float(qty),
        leverage=WHALE_LEVERAGE,
        strategy=strategy_name,
        entry_reason=reason,
        notes=f"ATR={atr:.4f} SL={sl_str} TP={tp_str}",
    )

    if notify_trade_opened:
        try:
            notify_trade_opened(
                symbol=signal.weex_symbol,
                entry_price=price,
                quantity=qty,                       # str — notifier converts internally
                leverage=WHALE_LEVERAGE,
                sl_price=levels["sl"],
                tp1_price=levels["tp"],             # whale uses single TP, not partial
                tp2_price=levels["tp"],             # passing same value for both fields
                atr_at_entry=atr,
                strategy=strategy_name,
                entry_reason=reason,
                direction=signal.direction,
            )
        except Exception as e:
            logger.warning("Notifier error on open: %s", e)

    return True


def close_whale_position(
    executor: Executor,
    state: dict,
    key: str,
    reason: str,
) -> bool:
    """Close a whale position at market and clean up state."""
    pos = state.get("positions", {}).get(key)
    if not pos:
        return False

    symbol = pos.get("symbol", key.replace(WHALE_STATE_KEY_PREFIX, "") + "USDT")
    direction = pos.get("direction", "LONG")

    logger.info("CLOSING %s %s (reason: %s)", direction, symbol, reason)

    # Cancel any pending SL/TP orders before closing
    executor.cancel_pending_orders(symbol)

    if direction == "LONG":
        result = executor.close_long_full(symbol)
    else:
        result = executor.close_short_full(symbol)

    if not result.get("ok"):
        logger.error("Close order failed for %s: %s", symbol, result)
        return False

    exit_price = executor.get_symbol_price(symbol) or pos.get("entry_price", 0.0)

    # Extract coin back from the key for cooldown tagging
    coin = key.replace(WHALE_STATE_KEY_PREFIX, "")
    record_cooldown(state, coin)

    register_exit(state, key)
    save_state(state, owner="whale")

    log_trade(
        symbol=symbol,
        direction=direction,
        entry_price=pos.get("entry_price", 0.0),
        exit_price=exit_price,
        quantity=float(pos.get("quantity", 0)),
        leverage=WHALE_LEVERAGE,
        strategy=pos.get("strategy", f"{WHALE_STRATEGY_TAG} {coin}"),
        exit_reason=reason,
        notes=f"closed at ${exit_price:.4f}",
    )

    if notify_trade_closed:
        try:
            # Best-effort portfolio value for the email footer; falls back to 0
            # in DRY_RUN where WEEX returns zero balance.
            try:
                bal = executor.get_account_balance()
                portfolio_value = float(bal.get("balance", 0) or 0)
            except Exception:
                portfolio_value = 0.0
            notify_trade_closed(
                symbol=symbol,
                direction=direction,
                entry_price=pos.get("entry_price", 0.0),
                exit_price=exit_price,
                quantity=float(pos.get("quantity", 0)),
                leverage=WHALE_LEVERAGE,
                sl_price=pos.get("sl") or 0.0,
                tp1_price=pos.get("tp") or 0.0,
                tp2_price=pos.get("tp") or 0.0,
                exit_reason=reason,
                strategy=pos.get("strategy", ""),
                portfolio_value=portfolio_value,
            )
        except Exception as e:
            logger.warning("Notifier error on close: %s", e)

    return True


# ─── Per-cycle orchestration ─────────────────────────────────────────────────

def manage_open_positions(executor: Executor, state: dict,
                          smart_stats: dict) -> None:
    """For each open whale position, check SL/TP (by price) and signal-flip exit."""
    to_close: List[tuple] = []  # (key, reason)

    for key, pos in list(state.get("positions", {}).items()):
        if not key.startswith(WHALE_STATE_KEY_PREFIX):
            continue
        symbol = pos.get("symbol", "")
        direction = pos.get("direction", "LONG")
        sl = pos.get("sl")
        tp = pos.get("tp")
        entry = pos.get("entry_price", 0.0)

        price = executor.get_symbol_price(symbol)
        if price is None:
            logger.warning("No price for %s, skipping SL/TP check", symbol)
            continue

        # Manual SL/TP check (belt-and-suspenders; exchange also has stop orders)
        if direction == "LONG":
            if sl is not None and price <= sl:
                to_close.append((key, f"SL hit @ {price:.4f} (sl={sl:.4f})"))
                continue
            if tp is not None and price >= tp:
                to_close.append((key, f"TP hit @ {price:.4f} (tp={tp:.4f})"))
                continue
        else:  # SHORT
            if sl is not None and price >= sl:
                to_close.append((key, f"SL hit @ {price:.4f} (sl={sl:.4f})"))
                continue
            if tp is not None and price <= tp:
                to_close.append((key, f"TP hit @ {price:.4f} (tp={tp:.4f})"))
                continue

        # Signal-flip exit
        coin = key.replace(WHALE_STATE_KEY_PREFIX, "")
        dominant = compute_dominant_pct(coin, direction, smart_stats)
        if dominant is not None and dominant < SIGNAL_FLIP_THRESHOLD:
            to_close.append((key, f"signal flip: smart {direction.lower()} "
                                  f"dropped to {dominant:.0f}%"))
            continue

        logger.debug("%s: price=%.4f entry=%.4f SL=%.4f TP=%.4f dom=%s%% — holding",
                     key, price, entry, sl or 0, tp or 0,
                     f"{dominant:.0f}" if dominant is not None else "n/a")

    for key, reason in to_close:
        close_whale_position(executor, state, key, reason)


def log_signals_jsonl(signals: list) -> None:
    """Append every poll's signals to a JSONL file for offline analysis."""
    try:
        ts = datetime.now(timezone.utc).isoformat()
        with open(WHALE_SIGNAL_LOG, "a", encoding="utf-8") as f:
            for s in signals:
                record = {"timestamp": ts, **s.to_dict()}
                f.write(json.dumps(record) + "\n")
    except Exception as e:
        logger.warning("Signal log write failed: %s", e)


_PREV_POSITIONS_FILE = Path(__file__).resolve().parent / ".whale_prev_positions.json"


def _load_prev_snapshot() -> dict:
    """Load the previous cycle's position snapshot (for recency diff)."""
    if not _PREV_POSITIONS_FILE.exists():
        return {}
    try:
        return json.loads(_PREV_POSITIONS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Prev snapshot unreadable, starting fresh: %s", e)
        return {}


def _save_snapshot(snapshot: dict) -> None:
    try:
        _PREV_POSITIONS_FILE.write_text(json.dumps(snapshot), encoding="utf-8")
    except IOError as e:
        logger.warning("Failed to save snapshot for next cycle: %s", e)


def run_cycle(executor: Executor, state: dict, weex_whitelist: set) -> None:
    """One poll cycle: fetch whales, manage open positions, open new ones.

    Tier 1 confluence data (liq clusters, funding, recency) is computed once
    per cycle from the same cohort fetch and applied via enrich_signal().
    """
    logger.info("=" * 60)
    logger.info("Whale cycle starting at %s", datetime.now().isoformat())

    # 1. Fetch cohorts (one shared fetch — reused for signals AND position mgmt)
    try:
        smart_wallets, rekt_wallets = fetch_cohorts()
    except Exception as e:
        logger.error("Cohort fetch failed, skipping cycle: %s", e)
        return

    smart_stats = aggregate_cohort(smart_wallets)
    rekt_stats = aggregate_cohort(rekt_wallets)

    # 1b. Tier 1: extract liq clusters, HL funding/OI, and recency diff
    smart_liq = extract_liq_data(smart_wallets)
    rekt_liq = extract_liq_data(rekt_wallets)
    hl_ctx_map = fetch_meta_and_ctxs()  # {coin: HLContext}; empty dict on failure
    prev_snapshot = _load_prev_snapshot()
    curr_snapshot = build_position_snapshot(smart_wallets)

    # 2. Manage existing whale positions (signal-flip + SL/TP)
    manage_open_positions(executor, state, smart_stats)

    # 3. Generate signals, enriching each with confluence data
    all_coins = set(smart_stats.keys()) | set(rekt_stats.keys())
    signals = []
    for coin in all_coins:
        weex_sym = hl_coin_to_weex_symbol(coin, weex_whitelist)
        if weex_sym is None:
            continue
        smart = smart_stats.get(coin, CoinStats(coin=coin))
        rekt = rekt_stats.get(coin, CoinStats(coin=coin))
        sig = classify(coin, smart, rekt, weex_sym)
        if not sig:
            continue

        # Confluence enrichment: funding + liq cluster + recency
        hl_ctx = hl_ctx_map.get(coin)
        current_price = hl_ctx.mark_price if hl_ctx else 0.0
        liq_ctx = compute_liq_context(coin, sig.direction, current_price,
                                       smart_liq, rekt_liq) if current_price > 0 else None
        recency_ctx = compute_recency(coin, sig.direction, prev_snapshot, curr_snapshot)
        enrich_signal(sig, liq=liq_ctx, hl_ctx=hl_ctx, recency=recency_ctx)
        signals.append(sig)

    signals.sort(key=lambda s: s.score, reverse=True)
    log_signals_jsonl(signals)
    logger.info("Generated %d actionable signals (enriched)", len(signals))
    # Persist this cycle's snapshot for the next cycle's recency diff
    _save_snapshot(curr_snapshot)

    # 4. Gates before opening any new trade
    if not TRADING_ENABLED:
        logger.info("TRADING_ENABLED=false — not opening new trades this cycle.")
        return

    if WHALE_PAUSED:
        logger.info("WHALE_PAUSED=true — not opening new whale trades. Existing "
                    "positions still manage to exit. Set WHALE_PAUSED=false in .env "
                    "to resume.")
        return

    # Kill-switch: consecutive-loss breaker + global daily drawdown.
    # Existing positions still manage to exit; this only blocks new entries.
    try:
        from kill_switch import should_pause
        ks = should_pause("whale")
        if ks.paused:
            logger.warning("Kill-switch active for whale bot: %s", ks.reason)
            return
    except Exception as e:
        logger.warning("Kill-switch check failed (allowing entries): %s", e)

    recent_pnl = recent_whale_pnl(days=7)
    if recent_pnl < -WHALE_MAX_7D_LOSS_USD:
        logger.warning("Whale 7d PnL is $%.0f (< -$%.0f threshold). Pausing new entries.",
                       recent_pnl, WHALE_MAX_7D_LOSS_USD)
        return

    # 5. Attempt entries (top-ranked signals first)
    for sig in signals:
        if not can_open_new_position(state):
            logger.info("No more slots available (8/8 open) — stopping entry loop.")
            break
        key = _state_key(sig.coin)
        if key in state.get("positions", {}):
            continue
        if is_on_cooldown(state, sig.coin):
            logger.info("%s on cooldown, skipping", sig.coin)
            continue

        # Fetch ATR on 4H klines for SL/TP
        klines = executor.get_klines(sig.weex_symbol, WHALE_ATR_INTERVAL, 100)
        atr = compute_atr(klines, WHALE_ATR_PERIOD)
        if atr is None or atr <= 0:
            logger.warning("%s: could not compute ATR, skipping", sig.weex_symbol)
            continue

        open_whale_position(executor, state, sig, atr)

    # 6. Heartbeat — touch a file so the dashboard can show "live" status.
    try:
        heartbeat = Path(__file__).resolve().parent / ".whale_heartbeat"
        heartbeat.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    except Exception as e:
        logger.warning("Heartbeat write failed: %s", e)

    # 7. Regenerate the HTML dashboard so the Whale Bot tab reflects this cycle.
    if build_dashboard is not None:
        try:
            build_dashboard(executor, state)
        except Exception as e:
            logger.warning("Dashboard regen failed: %s", e)


# ─── Main loop ───────────────────────────────────────────────────────────────

def run():
    setup_logging()
    print_banner()

    executor = Executor(dry_run=DRY_RUN)

    # Pre-flight: account balance (tolerate missing creds in DRY_RUN)
    try:
        bal = executor.get_account_balance()
        if bal:
            logger.info("WEEX connected. Equity: %s  Available: %s",
                        bal.get("balance"), bal.get("availableBalance"))
    except SystemExit as e:
        if not DRY_RUN:
            logger.error("WEEX credentials missing in LIVE mode: %s", e)
            sys.exit(1)
        logger.warning("WEEX creds missing (%s). Running DRY_RUN.", e)

    # WEEX symbol whitelist
    weex_whitelist = load_weex_whitelist(executor)

    # Shared state
    state = load_state()
    open_whale = sum(1 for k in state.get("positions", {}) if k.startswith(WHALE_STATE_KEY_PREFIX))
    logger.info("State loaded: %d total open positions (%d whale)",
                len(state.get("positions", {})), open_whale)

    # Load contract info for all coins we might trade (lazy per-symbol)
    # For efficiency we cache the whole WEEX symbol list upfront.
    logger.info("Prewarming contract info cache...")
    try:
        executor.load_contract_info(sorted(weex_whitelist))
    except SystemExit:
        pass  # DRY_RUN, no creds — load_contract_info needs no auth, but fall through anyway

    cycle_count = 0
    logger.info("Entering whale-bot main loop (Ctrl+C to stop)...")
    while True:
        cycle_count += 1
        t0 = time.time()
        try:
            run_cycle(executor, state, weex_whitelist)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt — shutting down.")
            break
        except Exception as e:
            logger.exception("Cycle %d crashed: %s", cycle_count, e)

        elapsed = time.time() - t0
        sleep_for = max(1.0, WHALE_POLL_INTERVAL_SECONDS - elapsed)
        logger.info("Cycle %d done in %.1fs. Sleeping %.0fs.", cycle_count, elapsed, sleep_for)
        time.sleep(sleep_for)


if __name__ == "__main__":
    run()
