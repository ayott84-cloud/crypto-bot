"""Crypto Trading Bot — Main Loop Orchestrator.

Polls WEEX for candle data, calculates indicators, detects signals,
and executes trades automatically. DRY_RUN=True by default.

Usage:
    python main.py
"""

from __future__ import annotations

import logging
import math
import os
import sys
import time
from datetime import datetime, timezone

# Ensure bot directory is on path
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

from config import (
    ASSETS, DRY_RUN, POLL_INTERVAL_SECONDS, CANDLE_FETCH_COUNT,
    DEFAULT_LEVERAGE, LOG_FILE, DASHBOARD_REGEN_CYCLES, MAX_POSITIONS,
    MARGIN_PER_TRADE,
)
from signals import (
    build_dataframe, compute_indicators, check_entry_signal,
    check_exit_conditions, get_entry_reason, analyze_entry_signal,
)
from executor import Executor
from position_manager import (
    load_state, save_state, get_open_positions, count_open_positions,
    can_open_new_position, find_most_profitable_position,
    register_entry, register_tp1_taken, register_exit,
    increment_bar_count, calculate_position_quantity,
    reconcile_with_exchange,
)
from journal import log_trade, flush_pending

try:
    from dashboard import build_dashboard
except ImportError:
    build_dashboard = None

try:
    from notifier import notify_trade_opened, notify_trade_closed
except ImportError:
    notify_trade_opened = None
    notify_trade_closed = None

logger = logging.getLogger("crypto_bot")

VERSION = "1.0.0"


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _price_decimals(executor, symbol: str) -> int:
    """Get number of decimal places for price formatting."""
    tick = executor.get_tick_size(symbol)
    if tick >= 1:
        return 0
    return max(0, -int(math.floor(math.log10(tick))))


def _qty_decimals(qty_str: str) -> int:
    """Get decimal places from a quantity string."""
    if "." in qty_str:
        return len(qty_str.split(".")[-1])
    return 3


def _get_portfolio_value(executor) -> float:
    """Fetch current total portfolio equity from exchange."""
    try:
        bal = executor.get_account_balance()
        return float(bal.get("balance", 0) or 0)
    except (Exception, SystemExit):
        return 0.0


def setup_logging():
    """Configure dual logging: console + file."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Console handler (INFO)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # File handler (DEBUG)
    try:
        fh = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except IOError as e:
        print(f"Warning: Could not open log file: {e}")


def print_banner():
    """Print startup banner."""
    print()
    print("=" * 60)
    print("  CRYPTO TRADING BOT v{}".format(VERSION))
    print("=" * 60)
    print(f"  Mode:          {'DRY RUN (no real trades)' if DRY_RUN else 'LIVE TRADING'}")
    print(f"  Margin/Trade:  ${MARGIN_PER_TRADE} x {DEFAULT_LEVERAGE}x = ${MARGIN_PER_TRADE * DEFAULT_LEVERAGE} notional")
    print(f"  Max Positions: {MAX_POSITIONS}")
    print(f"  Poll Interval: {POLL_INTERVAL_SECONDS}s ({POLL_INTERVAL_SECONDS // 60}m)")
    print(f"  Assets:")
    for name, cfg in ASSETS.items():
        print(f"    {name:4s} -> {cfg['symbol']:10s} {cfg['interval']:3s}  ({cfg['strategy_name']})")
    print("=" * 60)
    print()


# ─── Main Loop ───────────────────────────────────────────────────────────────

def run():
    """Main bot loop."""
    setup_logging()
    print_banner()

    # Initialize executor
    executor = Executor(dry_run=DRY_RUN)

    # Test API connectivity (gracefully handle missing creds in DRY_RUN)
    logger.info("Testing WEEX API connectivity...")
    try:
        balance = executor.get_account_balance()
    except SystemExit as e:
        balance = None
        if not DRY_RUN:
            logger.error("WEEX credentials missing in LIVE mode: %s", e)
            sys.exit(1)
        else:
            logger.warning("WEEX credentials missing (%s). Running DRY_RUN with public market data only.", e)

    if balance:
        avail = balance.get("availableBalance", "?")
        equity = balance.get("balance", "?")
        logger.info("Connected! Equity: %s USDT, Available: %s USDT", equity, avail)
    else:
        if not DRY_RUN:
            logger.error("Cannot connect to WEEX API. Aborting (LIVE mode).")
            sys.exit(1)
        else:
            logger.warning("Cannot connect to WEEX API. Continuing in DRY RUN mode.")

    # Load contract info for all symbols
    all_symbols = [cfg["symbol"] for cfg in ASSETS.values()]
    logger.info("Loading contract info for %s...", all_symbols)
    executor.load_contract_info(all_symbols)

    # Load state
    state = load_state()
    logger.info("State loaded: %d open positions", count_open_positions(state))

    # Reconcile with exchange
    logger.info("Reconciling state with exchange positions...")
    try:
        reconcile_with_exchange(state, executor, owner="momentum")
    except SystemExit as e:
        if DRY_RUN:
            logger.warning("Skipping exchange reconciliation (no credentials, DRY_RUN): %s", e)
        else:
            raise

    # Main loop
    cycle_count = 0
    logger.info("Entering main loop (Ctrl+C to stop)...")

    while True:
        cycle_count += 1
        trade_occurred = False
        cycle_start = time.time()

        # v2: Pre-fetch BTC context for each timeframe used by any asset with BTC filter
        # (BTC itself doesn't need this; only alts with use_btc_filter=True)
        btc_context = {}  # interval → (btc_close, btc_ema)
        intervals_needing_btc = {
            cfg["interval"] for cfg in ASSETS.values()
            if cfg.get("use_btc_filter", False)
        }
        for tf in intervals_needing_btc:
            try:
                btc_klines = executor.get_klines("BTCUSDT", tf, 200)
                if btc_klines and len(btc_klines) >= 60:
                    import pandas_ta as _ta
                    btc_df = build_dataframe(btc_klines)
                    btc_close_val = float(btc_df.iloc[-2]["close"])
                    btc_ema_period = 50  # v2 standard
                    btc_ema_val = float(_ta.ema(btc_df["close"], length=btc_ema_period).iloc[-2])
                    btc_context[tf] = (btc_close_val, btc_ema_val)
                    logger.debug("BTC %s context: close=%.2f ema%d=%.2f bullish=%s",
                                 tf, btc_close_val, btc_ema_period, btc_ema_val,
                                 btc_close_val > btc_ema_val)
                else:
                    btc_context[tf] = (None, None)
                    logger.warning("Insufficient BTC %s klines for correlation filter", tf)
            except Exception as e:
                btc_context[tf] = (None, None)
                logger.error("Failed to fetch BTC %s for correlation filter: %s", tf, e)

        for asset_name, cfg in ASSETS.items():
            symbol = cfg["symbol"]       # exchange symbol (XRPUSDT) — for API calls
            state_key = asset_name        # unique per strategy (XRP, XRP_4H) — for state
            interval = cfg["interval"]

            try:
                # 1. Fetch klines
                raw_klines = executor.get_klines(symbol, interval, CANDLE_FETCH_COUNT)
                if not raw_klines or len(raw_klines) < 50:
                    logger.warning("[%s] Insufficient kline data (%d bars), skipping",
                                   asset_name, len(raw_klines) if raw_klines else 0)
                    continue

                # 2. Build DataFrame + compute indicators (every cycle for live diagnostics)
                df = build_dataframe(raw_klines)
                df = compute_indicators(df, cfg)

                # 3. ALWAYS compute signal diagnostics (for dashboard)
                btc_close_val, btc_ema_val = btc_context.get(interval, (None, None))
                try:
                    analysis = analyze_entry_signal(
                        df, cfg, btc_close=btc_close_val, btc_ema=btc_ema_val
                    )
                    # Log filter breakdown
                    def _sym(v):
                        if v is True:
                            return "✅"
                        if v is False:
                            return "❌"
                        return "➖"
                    filter_line = " ".join(
                        f"{k}:{_sym(v)}" for k, v in analysis["filters"].items() if v is not None
                    )
                    status_emoji = "🟢" if analysis["would_enter"] else "⚪"
                    logger.info("%s [%s] signal: would_enter=%s | blocked_by=%s | %s",
                                status_emoji, asset_name, analysis["would_enter"],
                                analysis["blocked_by"] or "none", filter_line)
                    # Persist to state for dashboard consumption
                    if "signal_status" not in state:
                        state["signal_status"] = {}
                    state["signal_status"][asset_name] = {
                        "symbol": symbol,
                        "interval": interval,
                        "strategy_name": cfg.get("strategy_name"),
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                        **analysis,
                    }
                    # Persist diagnostics immediately so dashboard always has fresh data
                    save_state(state, owner="momentum")
                except Exception as e:
                    logger.error("[%s] Diagnostic computation failed: %s", asset_name, e)
                    analysis = None

                # 4. Check for new candle close
                last_completed_time = raw_klines[-2][0]
                if isinstance(last_completed_time, str):
                    last_completed_time = int(last_completed_time)

                last_processed = state["last_processed_candle"].get(state_key, 0)

                if last_completed_time <= last_processed:
                    continue  # No new candle since last check; diagnostics already updated

                # New candle detected!
                state["last_processed_candle"][state_key] = last_completed_time
                candle_dt = datetime.fromtimestamp(
                    last_completed_time / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M UTC")
                logger.info("[%s] New %s candle closed at %s",
                            asset_name, interval, candle_dt)

                # 4. Check if we have an open position for this state_key
                positions = get_open_positions(state)

                if state_key in positions:
                    # ── Manage existing position ──
                    pos = positions[state_key]
                    bars = increment_bar_count(state, state_key)
                    current_price = float(df.iloc[-2]["close"])

                    exit_reason, exit_type = check_exit_conditions(
                        entry_price=pos["entry_price"],
                        atr_at_entry=pos["atr_at_entry"],
                        current_price=current_price,
                        bars_since_entry=bars,
                        phase=pos["phase"],
                        cfg=cfg,
                    )

                    if exit_reason:
                        logger.info("[%s] EXIT: %s | price=%.4f | bars=%d | phase=%s",
                                    asset_name, exit_reason, current_price,
                                    bars, pos["phase"])

                        if exit_type == "partial":
                            # TP1: close 50%
                            dec = _qty_decimals(pos["quantity"])
                            full_qty = float(pos["quantity"])
                            close_qty = full_qty * cfg["tp1_close_pct"]
                            remaining_qty = full_qty - close_qty
                            close_str = f"{close_qty:.{dec}f}"
                            remaining_str = f"{remaining_qty:.{dec}f}"

                            executor.close_long_partial(symbol, close_str)
                            executor.cancel_pending_orders(symbol)

                            # Place new SL at breakeven for remaining
                            pdec = _price_decimals(executor, symbol)
                            new_sl = f"{pos['entry_price']:.{pdec}f}"
                            executor.place_sl_order(symbol, new_sl, remaining_str)

                            register_tp1_taken(state, state_key, remaining_str)

                            log_trade(
                                symbol=symbol, direction="LONG",
                                entry_price=pos["entry_price"],
                                exit_price=current_price,
                                quantity=close_qty,
                                leverage=DEFAULT_LEVERAGE,
                                strategy=cfg["strategy_name"],
                                entry_reason=pos.get("entry_reason", ""),
                                exit_reason=exit_reason,
                                notes=f"Partial close ({cfg['tp1_close_pct']*100:.0f}%). "
                                      f"Remaining: {remaining_str}",
                            )

                            # Email notification — partial close
                            if notify_trade_closed:
                                tp1_p = pos["entry_price"] + cfg["tp1_atr_mult"] * pos["atr_at_entry"]
                                tp2_p = pos["entry_price"] + cfg["tp2_atr_mult"] * pos["atr_at_entry"]
                                sl_p = pos["entry_price"] - cfg["sl_atr_mult"] * pos["atr_at_entry"]
                                try:
                                    notify_trade_closed(
                                        symbol=symbol, direction="LONG",
                                        entry_price=pos["entry_price"],
                                        exit_price=current_price,
                                        quantity=close_qty,
                                        leverage=DEFAULT_LEVERAGE,
                                        sl_price=sl_p, tp1_price=tp1_p, tp2_price=tp2_p,
                                        exit_reason=exit_reason,
                                        strategy=cfg["strategy_name"],
                                        portfolio_value=_get_portfolio_value(executor),
                                        is_partial=True,
                                        notes=f"Partial close ({cfg['tp1_close_pct']*100:.0f}%). "
                                              f"Remaining: {remaining_str}",
                                    )
                                except Exception as e:
                                    logger.error("Email notification failed: %s", e)

                            trade_occurred = True

                        else:
                            # Full exit (TP2, SL, Stale)
                            executor.close_long_full(symbol)
                            executor.cancel_pending_orders(symbol)

                            register_exit(state, state_key)

                            log_trade(
                                symbol=symbol, direction="LONG",
                                entry_price=pos["entry_price"],
                                exit_price=current_price,
                                quantity=float(pos["quantity"]),
                                leverage=DEFAULT_LEVERAGE,
                                strategy=cfg["strategy_name"],
                                entry_reason=pos.get("entry_reason", ""),
                                exit_reason=exit_reason,
                                notes=f"Full close after {bars} bars. "
                                      f"Phase: {pos['phase']}",
                            )

                            # Email notification — full close
                            if notify_trade_closed:
                                tp1_p = pos["entry_price"] + cfg["tp1_atr_mult"] * pos["atr_at_entry"]
                                tp2_p = pos["entry_price"] + cfg["tp2_atr_mult"] * pos["atr_at_entry"]
                                sl_p = pos["entry_price"] - cfg["sl_atr_mult"] * pos["atr_at_entry"]
                                try:
                                    notify_trade_closed(
                                        symbol=symbol, direction="LONG",
                                        entry_price=pos["entry_price"],
                                        exit_price=current_price,
                                        quantity=float(pos["quantity"]),
                                        leverage=DEFAULT_LEVERAGE,
                                        sl_price=sl_p, tp1_price=tp1_p, tp2_price=tp2_p,
                                        exit_reason=exit_reason,
                                        strategy=cfg["strategy_name"],
                                        portfolio_value=_get_portfolio_value(executor),
                                        notes=f"Full close after {bars} bars. "
                                              f"Phase: {pos['phase']}",
                                    )
                                except Exception as e:
                                    logger.error("Email notification failed: %s", e)

                            trade_occurred = True

                else:
                    # ── Check for new entry signal ──
                    # Reuse the analysis already computed at top of cycle
                    if analysis is not None and analysis.get("would_enter"):
                        logger.info("[%s] ENTRY SIGNAL detected!", asset_name)

                        # Kill-switch check: consecutive-loss breaker + daily drawdown.
                        # Skip new entries (existing positions still manage to exit).
                        try:
                            from kill_switch import should_pause
                            ks = should_pause("momentum")
                            if ks.paused:
                                logger.warning("[%s] Kill-switch active: %s — skipping entry",
                                               asset_name, ks.reason)
                                continue
                        except Exception as e:
                            logger.warning("Kill-switch check failed (allowing entry): %s", e)

                        # Check position slots — only rotate momentum-owned positions.
                        # Whale-owned positions (WHALE_* prefix) are managed by the
                        # whale bot only; rotating them here would mis-direction the close.
                        if not can_open_new_position(state):
                            most_prof_key = find_most_profitable_position(
                                state, executor, owner="momentum"
                            )
                            if most_prof_key:
                                rot_pos = get_open_positions(state)[most_prof_key]
                                rot_symbol = rot_pos.get("symbol", most_prof_key)
                                # Defensive: read stored direction (momentum is LONG-only,
                                # but if that ever changes, this prevents a sign-flip bug).
                                rot_direction = rot_pos.get("direction", "LONG")
                                logger.info("Rotating out %s %s (%s) to make room for %s",
                                            most_prof_key, rot_direction, rot_symbol, asset_name)
                                rot_price = executor.get_symbol_price(rot_symbol)

                                if rot_direction == "LONG":
                                    executor.close_long_full(rot_symbol)
                                else:
                                    executor.close_short_full(rot_symbol)
                                executor.cancel_pending_orders(rot_symbol)
                                register_exit(state, most_prof_key)

                                log_trade(
                                    symbol=rot_symbol, direction=rot_direction,
                                    entry_price=rot_pos["entry_price"],
                                    exit_price=rot_price or rot_pos["entry_price"],
                                    quantity=float(rot_pos["quantity"]),
                                    leverage=DEFAULT_LEVERAGE,
                                    strategy=rot_pos.get("strategy", ""),
                                    entry_reason=rot_pos.get("entry_reason", ""),
                                    exit_reason=f"Rotated out for new {asset_name} signal",
                                    notes="Position slot rotation",
                                )

                                # Email notification — rotation close
                                if notify_trade_closed:
                                    # Look up config by state_key (asset_name), not symbol
                                    rot_cfg = ASSETS.get(most_prof_key, cfg)
                                    tp1_p = rot_pos["entry_price"] + rot_cfg["tp1_atr_mult"] * rot_pos["atr_at_entry"]
                                    tp2_p = rot_pos["entry_price"] + rot_cfg["tp2_atr_mult"] * rot_pos["atr_at_entry"]
                                    sl_p = rot_pos["entry_price"] - rot_cfg["sl_atr_mult"] * rot_pos["atr_at_entry"]
                                    try:
                                        notify_trade_closed(
                                            symbol=rot_symbol, direction=rot_direction,
                                            entry_price=rot_pos["entry_price"],
                                            exit_price=rot_price or rot_pos["entry_price"],
                                            quantity=float(rot_pos["quantity"]),
                                            leverage=DEFAULT_LEVERAGE,
                                            sl_price=sl_p, tp1_price=tp1_p, tp2_price=tp2_p,
                                            exit_reason=f"Rotated out for new {asset_name} signal",
                                            strategy=rot_pos.get("strategy", ""),
                                            portfolio_value=_get_portfolio_value(executor),
                                            notes="Position slot rotation",
                                        )
                                    except Exception as e:
                                        logger.error("Email notification failed: %s", e)

                                trade_occurred = True

                        # Get entry data from last completed candle
                        current_price = float(df.iloc[-2]["close"])
                        atr_at_entry = float(df.iloc[-2]["atr"])

                        qty = calculate_position_quantity(
                            symbol, current_price, DEFAULT_LEVERAGE, executor
                        )

                        # Calculate SL price
                        sl_price = current_price - cfg["sl_atr_mult"] * atr_at_entry
                        pdec = _price_decimals(executor, symbol)
                        sl_str = f"{sl_price:.{pdec}f}"

                        logger.info("[%s] OPENING LONG: qty=%s price=%.4f SL=%s ATR=%.4f",
                                    asset_name, qty, current_price, sl_str, atr_at_entry)

                        # Execute entry
                        executor.open_long(symbol, qty, sl_trigger_price=sl_str)

                        # Register in state
                        entry_reason = get_entry_reason(df, cfg)
                        register_entry(
                            state, state_key,
                            entry_price=current_price,
                            atr_at_entry=atr_at_entry,
                            quantity=qty,
                            strategy=cfg["strategy_name"],
                            entry_reason=entry_reason,
                            symbol=symbol,  # exchange symbol for API calls
                        )

                        # Place exchange-side SL as safety net
                        executor.place_sl_order(symbol, sl_str, qty)

                        # Email notification
                        if notify_trade_opened:
                            tp1_price = current_price + cfg["tp1_atr_mult"] * atr_at_entry
                            tp2_price = current_price + cfg["tp2_atr_mult"] * atr_at_entry
                            try:
                                notify_trade_opened(
                                    symbol=symbol,
                                    entry_price=current_price,
                                    quantity=qty,
                                    leverage=DEFAULT_LEVERAGE,
                                    sl_price=sl_price,
                                    tp1_price=tp1_price,
                                    tp2_price=tp2_price,
                                    atr_at_entry=atr_at_entry,
                                    strategy=cfg["strategy_name"],
                                    entry_reason=entry_reason,
                                )
                            except Exception as e:
                                logger.error("Email notification failed: %s", e)

                        trade_occurred = True

                save_state(state, owner="momentum")

            except Exception as e:
                logger.error("[%s] Error processing: %s", asset_name, e, exc_info=True)

        # Flush pending journal entries
        flush_pending()

        # Regenerate dashboard every cycle now (signal_status needs to stay fresh)
        # Was: every DASHBOARD_REGEN_CYCLES; now always so Entry Signal Diagnostics stays live
        try:
            if build_dashboard:
                build_dashboard(executor, state)
                if trade_occurred or (cycle_count % DASHBOARD_REGEN_CYCLES == 0):
                    logger.info("Dashboard regenerated (trade or periodic)")
        except Exception as e:
            logger.error("Dashboard generation failed: %s", e)

        # Cycle summary
        pos_count = count_open_positions(state)
        elapsed = time.time() - cycle_start
        logger.info("Cycle %d complete (%.1fs). Positions: %d/%d. Sleeping %ds...",
                     cycle_count, elapsed, pos_count, MAX_POSITIONS,
                     POLL_INTERVAL_SECONDS)

        time.sleep(POLL_INTERVAL_SECONDS)


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\n\nShutting down gracefully...")
        logger.info("Bot stopped by user (Ctrl+C)")
        sys.exit(0)
    except Exception as e:
        logger.critical("Fatal error: %s", e, exc_info=True)
        sys.exit(1)
