"""Phase F — ETH/BTC pair-trade bot main loop.

Opens and closes BOTH legs together as one logical position. Polls ETH
and BTC daily closes, computes the 30-day rolling z-score of the ratio,
and acts on entries/exits. Slot accounting via the PAIR_ namespace
extension landed in Phase F.3.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from pair_config import (
    PAIR_PAUSED, PAIR_POLL_INTERVAL_SECONDS, PAIR_INTERVAL,
    PAIR_STATE_KEY_PREFIX, PAIR_STRATEGY_TAG, PAIR_HEARTBEAT_FILE,
    PAIR_LONG_LEG_KEY, PAIR_SHORT_LEG_KEY,
    PAIR_LONG_SYMBOL, PAIR_SHORT_SYMBOL,
    PAIR_MARGIN_PER_LEG, PAIR_LEVERAGE, MAX_PAIR_POSITIONS,
    PAIR_CONFIG,
)
from pair_signals import (
    compute_ratio, rolling_z_score, analyze_pair_entry, check_pair_exit,
)
from executor import Executor
from journal import log_trade
from position_manager import (
    load_state, save_state, register_entry, register_exit,
)

logger = logging.getLogger("crypto_bot.pair_main")
_HEARTBEAT_FILE = PAIR_HEARTBEAT_FILE


def _write_heartbeat(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except Exception as e:
        logger.warning("Failed to write heartbeat: %s", e)


def _closes_from_klines(raw_klines: list) -> pd.Series:
    """Extract close prices from WEEX positional klines via signals helper."""
    from signals import build_dataframe
    if not raw_klines:
        return pd.Series([], dtype=float)
    df = build_dataframe(raw_klines)
    return df["close"].reset_index(drop=True)


def _pair_is_open(state: dict) -> bool:
    keys = state.get("positions", {})
    return PAIR_LONG_LEG_KEY in keys or PAIR_SHORT_LEG_KEY in keys


def open_pair_position(
    executor: Executor, state: dict, direction: str,
    eth_price: float, btc_price: float, current_ratio: float, z: float,
) -> None:
    """Open BOTH legs as one logical trade.

    direction: "LONG_ETH_SHORT_BTC" or "SHORT_ETH_LONG_BTC"
    """
    is_long_eth = direction == "LONG_ETH_SHORT_BTC"

    notional = PAIR_MARGIN_PER_LEG * PAIR_LEVERAGE  # $500 per leg
    eth_qty = round(notional / eth_price, 6)
    btc_qty = round(notional / btc_price, 6)
    if eth_qty <= 0 or btc_qty <= 0:
        logger.warning("Pair-open: computed qty <= 0; aborting")
        return

    logger.info("PAIR OPEN %s | ratio=%.6f z=%.2f | ETH qty=%s @ %.2f, BTC qty=%s @ %.2f",
                direction, current_ratio, z, eth_qty, eth_price, btc_qty, btc_price)

    try:
        if is_long_eth:
            executor.open_long(PAIR_LONG_SYMBOL,   eth_qty)
            executor.open_short(PAIR_SHORT_SYMBOL, btc_qty)
        else:
            executor.open_short(PAIR_LONG_SYMBOL, eth_qty)
            executor.open_long(PAIR_SHORT_SYMBOL, btc_qty)
    except Exception as e:
        logger.error("Pair-open exchange call failed: %s", e)
        return

    reason = f"Pair entry z={z:.2f} ratio={current_ratio:.6f}"
    register_entry(
        state, PAIR_LONG_LEG_KEY,
        entry_price=eth_price, atr_at_entry=0.0,
        quantity=eth_qty, strategy=PAIR_STRATEGY_TAG,
        entry_reason=reason, symbol=PAIR_LONG_SYMBOL,
        direction="LONG" if is_long_eth else "SHORT",
        entry_ratio=current_ratio, entry_z=z,
    )
    register_entry(
        state, PAIR_SHORT_LEG_KEY,
        entry_price=btc_price, atr_at_entry=0.0,
        quantity=btc_qty, strategy=PAIR_STRATEGY_TAG,
        entry_reason=reason, symbol=PAIR_SHORT_SYMBOL,
        direction="SHORT" if is_long_eth else "LONG",
        entry_ratio=current_ratio, entry_z=z,
    )

    # Email open notification — single email for the pair (mentions both legs)
    try:
        from notifier import notify_trade_opened
        notify_trade_opened(
            symbol=f"ETH/BTC pair ({direction})",
            entry_price=current_ratio,
            quantity=f"{eth_qty} ETH / {btc_qty} BTC",
            leverage=PAIR_LEVERAGE,
            sl_price=current_ratio * 1.05 if is_long_eth else current_ratio * 0.95,
            tp1_price=current_ratio if abs(z) < 0.5 else current_ratio * (1 - 0.5 * z / abs(z)),
            tp2_price=current_ratio,
            atr_at_entry=0.0,
            strategy=PAIR_STRATEGY_TAG,
            entry_reason=f"z-score {z:+.2f}, ratio {current_ratio:.6f}",
            direction="LONG" if is_long_eth else "SHORT",
        )
    except Exception as e:
        logger.warning("pair-open notification failed: %s", e)


def close_pair_position(executor: Executor, state: dict, reason: str) -> None:
    """Close BOTH legs of the pair. Reason applied to both journal rows."""
    positions = state.get("positions", {})
    long_leg  = positions.get(PAIR_LONG_LEG_KEY)
    short_leg = positions.get(PAIR_SHORT_LEG_KEY)
    if not (long_leg or short_leg):
        return

    def _close_one(state_key, pos):
        if not pos:
            return
        symbol = pos.get("symbol", "")
        direction = pos.get("direction", "LONG")
        exit_price = executor.get_symbol_price(symbol) or pos.get("entry_price")
        try:
            if direction == "SHORT":
                executor.close_short_full(symbol)
            else:
                executor.close_long_full(symbol)
            executor.cancel_pending_orders(symbol)
        except Exception as e:
            logger.error("[%s] exchange close failed: %s", state_key, e)
            return
        register_exit(state, state_key)
        try:
            log_trade(
                symbol=symbol, direction=direction,
                entry_price=pos["entry_price"],
                exit_price=exit_price or pos["entry_price"],
                quantity=float(pos["quantity"]),
                leverage=PAIR_LEVERAGE,
                strategy=pos.get("strategy", PAIR_STRATEGY_TAG),
                entry_reason=pos.get("entry_reason", ""),
                exit_reason=reason,
                date_closed=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.error("[%s] log_trade failed: %s", state_key, e)

    _close_one(PAIR_LONG_LEG_KEY,  long_leg)
    _close_one(PAIR_SHORT_LEG_KEY, short_leg)
    logger.info("PAIR CLOSED — %s", reason)

    # Email close notification — single email for the pair
    try:
        from notifier import notify_trade_closed
        # Combine both legs' PnL into one report (use long_leg as anchor)
        anchor = long_leg or short_leg or {}
        entry = float(anchor.get("entry_ratio") or anchor.get("entry_price") or 0)
        # Best-effort current price for ratio display
        try:
            eth_now = executor.get_symbol_price(PAIR_LONG_SYMBOL) or 0
            btc_now = executor.get_symbol_price(PAIR_SHORT_SYMBOL) or 1
            current_ratio = float(eth_now) / float(btc_now) if btc_now else entry
        except Exception:
            current_ratio = entry
        portfolio_value = 0.0
        try:
            bal = executor.get_account_balance()
            portfolio_value = float(bal.get("balance", 0) if bal else 0)
        except Exception:
            pass
        notify_trade_closed(
            symbol="ETH/BTC pair",
            direction=anchor.get("direction", "LONG"),
            entry_price=entry,
            exit_price=current_ratio,
            quantity=float(anchor.get("quantity") or 0),
            leverage=PAIR_LEVERAGE,
            sl_price=entry * 1.05,
            tp1_price=entry,
            tp2_price=entry,
            exit_reason=reason,
            strategy=PAIR_STRATEGY_TAG,
            portfolio_value=portfolio_value,
        )
    except Exception as e:
        logger.warning("pair-close notification failed: %s", e)


def run_cycle(executor: Executor, state: dict) -> None:
    _write_heartbeat(_HEARTBEAT_FILE)

    # Always pull latest prices for both symbols
    try:
        eth_klines = executor.get_klines(
            PAIR_LONG_SYMBOL,  PAIR_INTERVAL, max(60, PAIR_CONFIG["z_window"] * 2))
        btc_klines = executor.get_klines(
            PAIR_SHORT_SYMBOL, PAIR_INTERVAL, max(60, PAIR_CONFIG["z_window"] * 2))
    except Exception as e:
        logger.error("Failed to fetch klines: %s", e)
        save_state(state, owner="pair")
        return

    eth_close = _closes_from_klines(eth_klines)
    btc_close = _closes_from_klines(btc_klines)

    # 1. Exit-management for an open pair
    if _pair_is_open(state):
        long_leg = state.get("positions", {}).get(PAIR_LONG_LEG_KEY) or {}
        bars_held = int(long_leg.get("bars_held") or 0) + 1
        entry_ratio = float(long_leg.get("entry_ratio") or 0.0)
        # Reconstruct direction
        pos_dir = "LONG_ETH_SHORT_BTC" if long_leg.get("direction") == "LONG" \
                  else "SHORT_ETH_LONG_BTC"
        reason, _kind = check_pair_exit(
            eth_close, btc_close,
            position_direction=pos_dir,
            bars_held=bars_held, entry_ratio=entry_ratio,
            cfg=PAIR_CONFIG,
        )
        if reason:
            close_pair_position(executor, state, reason)
        else:
            # Bump bars_held on both legs
            for k in (PAIR_LONG_LEG_KEY, PAIR_SHORT_LEG_KEY):
                if k in state.get("positions", {}):
                    state["positions"][k]["bars_held"] = bars_held

    # 2. Pause flag short-circuits new entries
    if PAIR_PAUSED:
        save_state(state, owner="pair")
        return

    # 3. Capacity check — one pair at a time during validation
    if _pair_is_open(state):
        save_state(state, owner="pair")
        return

    # 4. New entry
    sig = analyze_pair_entry(eth_close, btc_close, PAIR_CONFIG)
    if sig["would_enter"]:
        eth_price = float(eth_close.iloc[-1])
        btc_price = float(btc_close.iloc[-1])
        open_pair_position(
            executor, state, sig["direction"],
            eth_price=eth_price, btc_price=btc_price,
            current_ratio=sig["ratio"] or 0.0, z=sig["z"] or 0.0,
        )

    save_state(state, owner="pair")


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Pair bot starting. PAUSED=%s", PAIR_PAUSED)

    executor = Executor()
    state = load_state()

    cycle = 0
    while True:
        cycle += 1
        t0 = time.time()
        try:
            run_cycle(executor, state)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt — shutting down.")
            break
        except Exception as e:
            logger.exception("Cycle %d crashed: %s", cycle, e)
        elapsed = time.time() - t0
        sleep_for = max(1.0, PAIR_POLL_INTERVAL_SECONDS - elapsed)
        logger.info("Cycle %d done in %.1fs. Sleeping %.0fs.", cycle, elapsed, sleep_for)
        time.sleep(sleep_for)


if __name__ == "__main__":
    run()
