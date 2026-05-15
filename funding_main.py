"""Funding-Fade Bot — Main Loop.

Polls per-coin funding rates, classifies extremes (97th percentile of rolling
30-day distribution + 0.05%/8h absolute floor), and fades the crowded side
on WEEX perpetuals. Independent of momentum and whale bots — same state.json
shared via FUNDING_* state-key prefix, same journal/dashboard/notifier.

Usage:
    python funding_main.py

Cadence: hourly poll. Entries are only considered inside the 60-min window
around each 8h funding fixing (00, 08, 16 UTC). Outside that window the bot
just refreshes the rolling history and manages existing positions toward exit.
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

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

from config import DRY_RUN, TRADING_ENABLED, LOG_FILE, MAX_POSITIONS
from funding_config import (
    FUNDING_PAUSED,
    FUNDING_POLL_INTERVAL_SECONDS,
    FUNDING_EXECUTION_WINDOW_MINUTES,
    FUNDING_FIXING_HOURS_UTC,
    FUNDING_PERCENTILE_THRESHOLD,
    FUNDING_ABSOLUTE_FLOOR,
    FUNDING_MIN_OI_USD,
    FUNDING_COOLDOWN_HOURS,
    FUNDING_MARGIN_USD, FUNDING_LEVERAGE,
    FUNDING_SL_ATR_MULT, FUNDING_TP_ATR_MULT,
    FUNDING_TIME_STOP_HOURS,
    FUNDING_NORMALIZE_TO_INNER_BAND,
    FUNDING_NORMALIZE_INNER_LOW_PCTILE, FUNDING_NORMALIZE_INNER_HIGH_PCTILE,
    FUNDING_STATE_KEY_PREFIX, FUNDING_STRATEGY_TAG,
    FUNDING_SIGNAL_LOG, FUNDING_HEARTBEAT,
    FUNDING_ATR_PERIOD, FUNDING_ATR_INTERVAL, FUNDING_ATR_SMA_PERIOD,
    FUNDING_TREND_EMA_PERIOD,
)
from funding_signals import (
    classify, compute_atr_and_sma, compute_ema_and_slope, trend_allows_fade,
    in_execution_window,
    CROWDED_LONG_FADE, CROWDED_SHORT_FADE,
)
from funding_history import fetch_history, percentile_of, get_distribution
from executor import Executor
from position_manager import (
    load_state, save_state, get_open_positions,
    register_entry, register_exit, can_open_new_position,
)
from journal import log_trade
from whale_hl_data import fetch_meta_and_ctxs
from whale_universe import get_top_symbols, hl_coin_to_weex_symbol  # noqa: F401 (hl_coin_to_weex_symbol unused but mirrors whale pattern)

try:
    from notifier import notify_trade_opened, notify_trade_closed
except ImportError:
    notify_trade_opened = None
    notify_trade_closed = None

try:
    from dashboard import build_dashboard
except ImportError:
    build_dashboard = None

logger = logging.getLogger("funding_bot")

VERSION = "1.0.0"


# ─── Logging + banner ────────────────────────────────────────────────────────

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
    print(f"  FUNDING-FADE BOT v{VERSION}")
    print("=" * 60)
    print(f"  Mode:              {'DRY RUN' if DRY_RUN else 'LIVE TRADING'}")
    print(f"  Trading enabled:   {TRADING_ENABLED}")
    print(f"  Funding paused:    {FUNDING_PAUSED}  {'(no new entries)' if FUNDING_PAUSED else ''}")
    print(f"  Poll interval:     {FUNDING_POLL_INTERVAL_SECONDS}s ({FUNDING_POLL_INTERVAL_SECONDS // 60}m)")
    print(f"  Sizing:            ${FUNDING_MARGIN_USD} × {FUNDING_LEVERAGE}x = ${FUNDING_MARGIN_USD * FUNDING_LEVERAGE} notional")
    print(f"  Threshold:         {FUNDING_PERCENTILE_THRESHOLD:.0f}th pctile + {FUNDING_ABSOLUTE_FLOOR:.4f} floor")
    print(f"  Execution window:  ±{FUNDING_EXECUTION_WINDOW_MINUTES}min around UTC {FUNDING_FIXING_HOURS_UTC}")
    print(f"  Max positions (shared with other bots): {MAX_POSITIONS}")
    print("=" * 60)
    print()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _state_key(coin: str) -> str:
    return f"{FUNDING_STATE_KEY_PREFIX}{coin.upper()}"


def _cooldown_map(state: dict) -> dict:
    return state.setdefault("funding_cooldowns", {})


def is_on_cooldown(state: dict, coin: str) -> bool:
    cd = _cooldown_map(state)
    ts = cd.get(coin)
    if not ts:
        return False
    try:
        exited = datetime.fromisoformat(ts)
    except ValueError:
        return False
    hours_since = (datetime.now(timezone.utc) - (exited.replace(tzinfo=timezone.utc)
                                                  if exited.tzinfo is None
                                                  else exited)).total_seconds() / 3600
    return hours_since < FUNDING_COOLDOWN_HOURS


def record_cooldown(state: dict, coin: str) -> None:
    _cooldown_map(state)[coin] = datetime.now(timezone.utc).isoformat()


def calc_quantity(symbol: str, price: float, margin_usd: float, executor: Executor) -> str:
    """Compute quantity respecting WEEX step/min."""
    notional = margin_usd * FUNDING_LEVERAGE
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


def compute_trade_levels(entry_price: float, atr: float, direction: str) -> dict:
    sl_dist = FUNDING_SL_ATR_MULT * atr
    tp_dist = FUNDING_TP_ATR_MULT * atr
    if direction == "LONG":
        return {"sl": entry_price - sl_dist, "tp": entry_price + tp_dist}
    return {"sl": entry_price + sl_dist, "tp": entry_price - tp_dist}


def log_signals_jsonl(signals: list) -> None:
    try:
        ts = datetime.now(timezone.utc).isoformat()
        with open(FUNDING_SIGNAL_LOG, "a", encoding="utf-8") as f:
            for s in signals:
                f.write(json.dumps({"timestamp": ts, **s.to_dict()}) + "\n")
    except Exception as e:
        logger.warning("Signal log write failed: %s", e)


# ─── Entry / exit ────────────────────────────────────────────────────────────

def open_funding_position(executor: Executor, state: dict, sig, atr: float) -> bool:
    key = _state_key(sig.coin)
    if key in state.get("positions", {}):
        logger.info("%s already open, skipping re-entry", key)
        return False
    if not can_open_new_position(state):
        logger.info("Global slot cap reached, cannot open %s", key)
        return False

    price = executor.get_symbol_price(sig.weex_symbol)
    if not price or price <= 0:
        logger.warning("%s: no price from WEEX, skipping", sig.weex_symbol)
        return False

    qty = calc_quantity(sig.weex_symbol, price, FUNDING_MARGIN_USD, executor)
    levels = compute_trade_levels(price, atr, sig.direction)
    tick = executor.get_tick_size(sig.weex_symbol)
    decimals = max(0, -int(math.log10(tick))) if 0 < tick < 1 else 2
    sl_str = f"{levels['sl']:.{decimals}f}"
    tp_str = f"{levels['tp']:.{decimals}f}"

    logger.info("OPENING %s %s @ %.*f qty=%s SL=%s TP=%s (margin=$%.0f, %s conf=%d, %s)",
                sig.direction, sig.weex_symbol, decimals, price, qty,
                sl_str, tp_str, FUNDING_MARGIN_USD, sig.signal, sig.confidence,
                sig.reasoning)

    if sig.direction == "LONG":
        result = executor.open_long(sig.weex_symbol, qty, sl_trigger_price=sl_str)
    else:
        result = executor.open_short(sig.weex_symbol, qty, sl_trigger_price=sl_str)
    if not result.get("ok"):
        logger.error("Open order failed for %s: %s", sig.weex_symbol, result)
        return False

    # TP order (server-side, separate from attached SL)
    tp_res = executor.place_tp_order(sig.weex_symbol, sig.direction, tp_str, qty)
    if not tp_res.get("ok"):
        logger.warning("TP order failed for %s (position still open): %s",
                       sig.weex_symbol, tp_res)

    strategy_name = f"{FUNDING_STRATEGY_TAG} {sig.coin} {sig.direction}"
    reason = (f"{sig.signal} | funding {sig.current_funding_annual_pct:+.1f}%/yr "
              f"(pct {sig.percentile:.0f}) | OI ${sig.oi_usd/1e6:.0f}M | "
              f"ATR {sig.atr:.4f}/sma {sig.atr_sma:.4f}")

    register_entry(
        state,
        state_key=key,
        entry_price=price,
        atr_at_entry=atr,
        quantity=qty,
        strategy=strategy_name,
        entry_reason=reason,
        symbol=sig.weex_symbol,
    )
    pos = state["positions"][key]
    pos["direction"] = sig.direction
    pos["sl"] = levels["sl"]
    pos["tp"] = levels["tp"]
    pos["signal_type"] = sig.signal
    pos["confidence"] = sig.confidence
    pos["margin_usd"] = FUNDING_MARGIN_USD
    pos["funding_at_entry"] = sig.current_funding
    pos["entry_time_iso"] = datetime.now(timezone.utc).isoformat()
    save_state(state, owner="funding")

    log_trade(
        symbol=sig.weex_symbol, direction=sig.direction,
        entry_price=price, exit_price=None,
        quantity=float(qty), leverage=FUNDING_LEVERAGE,
        strategy=strategy_name, entry_reason=reason,
        notes=f"ATR={atr:.4f} SL={sl_str} TP={tp_str} pct={sig.percentile:.0f}",
    )

    if notify_trade_opened:
        try:
            notify_trade_opened(
                symbol=sig.weex_symbol, entry_price=price, quantity=qty,
                leverage=FUNDING_LEVERAGE,
                sl_price=levels["sl"], tp1_price=levels["tp"], tp2_price=levels["tp"],
                atr_at_entry=atr, strategy=strategy_name,
                entry_reason=reason, direction=sig.direction,
            )
        except Exception as e:
            logger.warning("Notifier error on open: %s", e)
    return True


def close_funding_position(executor: Executor, state: dict, key: str, reason: str) -> bool:
    pos = state.get("positions", {}).get(key)
    if not pos:
        return False
    symbol = pos.get("symbol", "")
    direction = pos.get("direction", "LONG")
    logger.info("CLOSING %s %s (%s)", direction, symbol, reason)

    executor.cancel_pending_orders(symbol)
    if direction == "LONG":
        result = executor.close_long_full(symbol)
    else:
        result = executor.close_short_full(symbol)
    if not result.get("ok"):
        logger.error("Close failed for %s: %s", symbol, result)
        return False

    exit_price = executor.get_symbol_price(symbol) or pos.get("entry_price", 0.0)
    coin = key.replace(FUNDING_STATE_KEY_PREFIX, "")
    record_cooldown(state, coin)
    register_exit(state, key)
    save_state(state, owner="funding")

    log_trade(
        symbol=symbol, direction=direction,
        entry_price=pos.get("entry_price", 0.0), exit_price=exit_price,
        quantity=float(pos.get("quantity", 0)), leverage=FUNDING_LEVERAGE,
        strategy=pos.get("strategy", f"{FUNDING_STRATEGY_TAG} {coin}"),
        exit_reason=reason, notes=f"closed at ${exit_price:.4f}",
        date_closed=datetime.now(timezone.utc),
    )

    if notify_trade_closed:
        try:
            bal = executor.get_account_balance()
            portfolio_value = float(bal.get("balance", 0) or 0)
        except Exception:
            portfolio_value = 0.0
        try:
            notify_trade_closed(
                symbol=symbol, direction=direction,
                entry_price=pos.get("entry_price", 0.0),
                exit_price=exit_price,
                quantity=float(pos.get("quantity", 0)),
                leverage=FUNDING_LEVERAGE,
                sl_price=pos.get("sl") or 0.0,
                tp1_price=pos.get("tp") or 0.0, tp2_price=pos.get("tp") or 0.0,
                exit_reason=reason, strategy=pos.get("strategy", ""),
                portfolio_value=portfolio_value,
            )
        except Exception as e:
            logger.warning("Notifier error on close: %s", e)
    return True


def manage_open_positions(executor: Executor, state: dict,
                           hl_ctx_map: dict) -> None:
    """Close on: SL hit, TP hit, time-stop (8h), funding-normalize."""
    now = datetime.now(timezone.utc)
    to_close: List[tuple] = []
    for key, pos in list(state.get("positions", {}).items()):
        if not key.startswith(FUNDING_STATE_KEY_PREFIX):
            continue
        symbol = pos.get("symbol", "")
        direction = pos.get("direction", "LONG")
        sl = pos.get("sl"); tp = pos.get("tp")
        price = executor.get_symbol_price(symbol)
        if price is None:
            logger.warning("No price for %s, skipping", symbol); continue

        # SL / TP
        if direction == "LONG":
            if sl is not None and price <= sl:
                to_close.append((key, f"SL hit @ {price:.4f}")); continue
            if tp is not None and price >= tp:
                to_close.append((key, f"TP hit @ {price:.4f}")); continue
        else:
            if sl is not None and price >= sl:
                to_close.append((key, f"SL hit @ {price:.4f}")); continue
            if tp is not None and price <= tp:
                to_close.append((key, f"TP hit @ {price:.4f}")); continue

        # Time-stop
        entry_iso = pos.get("entry_time_iso") or pos.get("entry_time")
        if entry_iso:
            try:
                entry_dt = datetime.fromisoformat(entry_iso)
                if entry_dt.tzinfo is None:
                    entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                hours_held = (now - entry_dt).total_seconds() / 3600
                if hours_held >= FUNDING_TIME_STOP_HOURS:
                    to_close.append((key, f"time-stop ({hours_held:.1f}h)")); continue
            except ValueError:
                pass

        # Funding-normalize exit
        if FUNDING_NORMALIZE_TO_INNER_BAND:
            coin = key.replace(FUNDING_STATE_KEY_PREFIX, "")
            hl_ctx = hl_ctx_map.get(coin)
            if hl_ctx is not None:
                hist = get_distribution(symbol, days=30)
                if hist:
                    pct = percentile_of(hl_ctx.funding_rate, hist)
                    if pct is not None and (FUNDING_NORMALIZE_INNER_LOW_PCTILE
                                            <= pct <= FUNDING_NORMALIZE_INNER_HIGH_PCTILE):
                        to_close.append((key, f"funding normalized (pct {pct:.0f})"))
                        continue

    for key, reason in to_close:
        close_funding_position(executor, state, key, reason)


# ─── Per-cycle orchestration ─────────────────────────────────────────────────

def run_cycle(executor: Executor, state: dict, weex_whitelist: set) -> None:
    logger.info("=" * 60)
    logger.info("Funding cycle starting at %s", datetime.now(timezone.utc).isoformat())

    # Refresh HL market context (funding + OI + mark prices) — single call
    hl_ctx_map = fetch_meta_and_ctxs()
    if not hl_ctx_map:
        logger.error("HL market context fetch failed; skipping cycle")
        return

    # Manage existing positions every cycle, even outside execution window
    manage_open_positions(executor, state, hl_ctx_map)

    # Refresh rolling history for top-100 coins (cheap, mostly cache hits)
    top_universe = get_top_symbols()
    candidates = []
    for coin in top_universe:
        weex_sym = f"{coin}USDT"
        if weex_sym not in weex_whitelist:
            continue
        hl_ctx = hl_ctx_map.get(coin)
        if hl_ctx is None:
            continue
        # Pull 30d history (HL fundingHistory, cached 12h)
        history = get_distribution(weex_sym, days=30)
        candidates.append((coin, weex_sym, hl_ctx, history))

    # Outside execution window → log signals for visibility but don't trade
    in_window = in_execution_window(window_minutes=FUNDING_EXECUTION_WINDOW_MINUTES,
                                     fixing_hours_utc=FUNDING_FIXING_HOURS_UTC)
    logger.info("Universe: %d candidates · execution_window=%s",
                len(candidates), in_window)

    signals = []
    for coin, weex_sym, hl_ctx, history in candidates:
        # Pull klines once for ATR + EMA
        klines = executor.get_klines(weex_sym, FUNDING_ATR_INTERVAL, 100)
        atr, atr_sma = compute_atr_and_sma(
            klines, period=FUNDING_ATR_PERIOD, sma_period=FUNDING_ATR_SMA_PERIOD
        )
        ema, slope_sign = compute_ema_and_slope(klines, period=FUNDING_TREND_EMA_PERIOD)
        last_close = float(klines[-1][4]) if klines else 0.0

        # Determine direction first so we can check trend filter
        # (cheap pre-check: skip if not extreme)
        from funding_history import is_extreme
        extreme = is_extreme(hl_ctx.funding_rate, history,
                             percentile_threshold=FUNDING_PERCENTILE_THRESHOLD,
                             absolute_floor=FUNDING_ABSOLUTE_FLOOR)
        if extreme is None:
            continue
        direction = "SHORT" if extreme == "top" else "LONG"
        trend_ok = trend_allows_fade(direction, last_close, ema, slope_sign)

        sig = classify(
            coin=coin, weex_symbol=weex_sym,
            current_funding=hl_ctx.funding_rate, history=history,
            oi_usd=hl_ctx.oi_usd,
            atr=atr or 0.0, atr_sma=atr_sma or 0.0,
            trend_ok=trend_ok,
        )
        if sig:
            signals.append((sig, atr or 0.0))

    log_signals_jsonl([s for s, _ in signals])
    logger.info("Generated %d funding-fade signals", len(signals))

    # Gate: pause flag, trading enabled, kill switch, execution window
    if not TRADING_ENABLED:
        logger.info("TRADING_ENABLED=false — no new entries")
        _heartbeat_and_regen(executor, state); return
    if FUNDING_PAUSED:
        logger.info("FUNDING_PAUSED=true — no new entries")
        _heartbeat_and_regen(executor, state); return
    try:
        from kill_switch import should_pause
        ks = should_pause("funding")
        if ks.paused:
            logger.warning("Kill-switch active for funding bot: %s", ks.reason)
            _heartbeat_and_regen(executor, state); return
    except Exception as e:
        logger.warning("Kill-switch check failed (allowing entries): %s", e)
    if not in_window:
        logger.info("Outside execution window — signals logged, no entries this cycle")
        _heartbeat_and_regen(executor, state); return

    # Sort signals by confidence (highest first) and attempt entries
    signals.sort(key=lambda x: x[0].confidence, reverse=True)
    for sig, atr in signals:
        if not can_open_new_position(state):
            logger.info("Slot cap reached — stopping entry loop"); break
        if is_on_cooldown(state, sig.coin):
            logger.info("%s on cooldown — skip", sig.coin); continue
        if atr <= 0:
            logger.warning("%s: no ATR — skip", sig.weex_symbol); continue
        open_funding_position(executor, state, sig, atr)

    _heartbeat_and_regen(executor, state)


def _heartbeat_and_regen(executor, state):
    """Touch heartbeat + regen dashboard."""
    try:
        FUNDING_HEARTBEAT.write_text(datetime.now(timezone.utc).isoformat(),
                                      encoding="utf-8")
    except Exception as e:
        logger.warning("Heartbeat write failed: %s", e)
    if build_dashboard is not None:
        try:
            build_dashboard(executor, state)
        except Exception as e:
            logger.warning("Dashboard regen failed: %s", e)


# ─── Main loop ───────────────────────────────────────────────────────────────

def _load_weex_whitelist(executor: Executor) -> set:
    """Reuse the whale-bot's WEEX whitelist (24h cached on disk)."""
    from whale_main import load_weex_whitelist
    return load_weex_whitelist(executor)


def run():
    setup_logging()
    print_banner()
    executor = Executor(dry_run=DRY_RUN)
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

    weex_whitelist = _load_weex_whitelist(executor)
    state = load_state()
    open_funding = sum(1 for k in state.get("positions", {})
                        if k.startswith(FUNDING_STATE_KEY_PREFIX))
    logger.info("State loaded: %d total open positions (%d funding)",
                len(state.get("positions", {})), open_funding)

    try:
        executor.load_contract_info(sorted(weex_whitelist))
    except SystemExit:
        pass

    cycle = 0
    logger.info("Entering funding-bot main loop (Ctrl+C to stop)...")
    while True:
        cycle += 1
        t0 = time.time()
        try:
            run_cycle(executor, state, weex_whitelist)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt — shutting down."); break
        except Exception as e:
            logger.exception("Cycle %d crashed: %s", cycle, e)
        elapsed = time.time() - t0
        sleep_for = max(1.0, FUNDING_POLL_INTERVAL_SECONDS - elapsed)
        logger.info("Cycle %d done in %.1fs. Sleeping %.0fs.", cycle, elapsed, sleep_for)
        time.sleep(sleep_for)


if __name__ == "__main__":
    run()
