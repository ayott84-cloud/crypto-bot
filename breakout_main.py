"""Phase G — Donchian breakout bot main loop.

Polls each asset in BREAKOUT_ASSETS, fetches klines, computes Donchian
channels + ATR + ATR_SMA + ADX, and acts on entries / exits via the
existing executor, journal, and state-manager plumbing.

State prefix: BREAKOUT_. The position_manager namespace was extended in
Phase G.3 so save/merge logic correctly partitions breakout positions
from momentum / whale / funding.
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from breakout_config import (
    BREAKOUT_PAUSED, BREAKOUT_POLL_INTERVAL_SECONDS,
    BREAKOUT_STATE_KEY_PREFIX, BREAKOUT_STRATEGY_TAG,
    BREAKOUT_HEARTBEAT_FILE, BREAKOUT_MARGIN_PER_TRADE, BREAKOUT_LEVERAGE,
    MAX_BREAKOUT_POSITIONS, BREAKOUT_ASSETS,
)
from breakout_signals import (
    compute_donchian_channels, analyze_breakout_entry, check_breakout_exit,
)
from executor import Executor
from journal import log_trade
from position_manager import (
    load_state, save_state, register_entry, register_exit,
)

logger = logging.getLogger("crypto_bot.breakout_main")
_HEARTBEAT_FILE = BREAKOUT_HEARTBEAT_FILE


def _write_heartbeat(path: Path) -> None:
    """Touch the heartbeat file so the dashboard shows breakout as LIVE.

    Must be called at the TOP of run_cycle, before any early-return on
    pause — otherwise a paused bot looks dead.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except Exception as e:
        logger.warning("Failed to write heartbeat: %s", e)


def _compute_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Add atr, atr_sma, and adx columns + Donchian channels for entry/exit."""
    import pandas_ta as ta

    df = df.copy()
    period = cfg.get("atr_period", 14)
    df["atr"]     = ta.atr(df["high"], df["low"], df["close"], length=period)
    df["atr_sma"] = df["atr"].rolling(cfg.get("atr_sma_period", 20)).mean()

    adx_df = ta.adx(df["high"], df["low"], df["close"],
                    length=cfg.get("adx_period", 14))
    if adx_df is not None and not adx_df.empty:
        # pandas_ta returns ADX in a column like 'ADX_14'
        adx_col = next((c for c in adx_df.columns if c.startswith("ADX_")), None)
        df["adx"] = adx_df[adx_col] if adx_col else 0.0
    else:
        df["adx"] = 0.0

    return df


def _build_dataframe(raw_klines: list) -> pd.DataFrame:
    """Convert raw kline rows to a DataFrame with open/high/low/close/volume floats."""
    df = pd.DataFrame(raw_klines)
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def open_breakout_position(
    executor: Executor, state: dict, asset_name: str, cfg: dict,
    df: pd.DataFrame, direction: str,
) -> None:
    """Place the order, register state, log the entry trade row."""
    symbol = cfg["symbol"]
    current_price = float(df.iloc[-1]["close"])
    atr_at_entry  = float(df.iloc[-1]["atr"])

    # Sizing: fixed margin × leverage / price
    notional = BREAKOUT_MARGIN_PER_TRADE * BREAKOUT_LEVERAGE
    qty = round(notional / current_price, 4)
    if qty <= 0:
        logger.warning("[%s] computed qty <= 0; skipping", asset_name)
        return

    sl_mult = (cfg.get("sl_atr_mult_short", 1.0)
               if direction == "SHORT" else cfg.get("sl_atr_mult", 1.5))
    sl_price = (current_price + sl_mult * atr_at_entry
                if direction == "SHORT"
                else current_price - sl_mult * atr_at_entry)
    sl_str = f"{sl_price:.6f}"

    logger.info("[%s] OPENING %s qty=%s price=%.4f SL=%s ATR=%.4f",
                asset_name, direction, qty, current_price, sl_str, atr_at_entry)

    try:
        if direction == "SHORT":
            executor.open_short(symbol, qty, sl_trigger_price=sl_str)
        else:
            executor.open_long(symbol, qty, sl_trigger_price=sl_str)
    except Exception as e:
        logger.error("[%s] exchange open failed: %s", asset_name, e)
        return

    state_key = f"{BREAKOUT_STATE_KEY_PREFIX}{asset_name}"
    register_entry(
        state, state_key,
        entry_price=current_price,
        atr_at_entry=atr_at_entry,
        quantity=qty,
        strategy=cfg.get("strategy_name", BREAKOUT_STRATEGY_TAG),
        entry_reason=f"Donchian break {direction}",
        symbol=symbol,
        direction=direction,
    )


def close_breakout_position(
    executor: Executor, state: dict, state_key: str, reason: str,
) -> None:
    """Close on exchange, strip state, write journal row."""
    pos = state.get("positions", {}).get(state_key)
    if not pos:
        logger.warning("close_breakout_position called for missing key %s", state_key)
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
        logger.error("[%s] exchange close failed: %s — state will retry next cycle",
                     state_key, e)
        return

    register_exit(state, state_key)

    try:
        log_trade(
            symbol=symbol, direction=direction,
            entry_price=pos["entry_price"],
            exit_price=exit_price or pos["entry_price"],
            quantity=float(pos["quantity"]),
            leverage=BREAKOUT_LEVERAGE,
            strategy=pos.get("strategy", BREAKOUT_STRATEGY_TAG),
            entry_reason=pos.get("entry_reason", ""),
            exit_reason=reason,
            date_closed=datetime.now(timezone.utc),
        )
    except Exception as e:
        logger.error("[%s] log_trade failed: %s — journal will be reconciled",
                     state_key, e)


def _count_open_breakouts(state: dict) -> int:
    return sum(1 for k in state.get("positions", {})
               if k.startswith(BREAKOUT_STATE_KEY_PREFIX))


def run_cycle(executor: Executor, state: dict) -> None:
    """One full poll: heartbeat → exits on open positions → entries on signals."""
    _write_heartbeat(_HEARTBEAT_FILE)

    # 1. Manage exits on existing breakout positions
    breakout_keys = [
        k for k in list(state.get("positions", {}).keys())
        if k.startswith(BREAKOUT_STATE_KEY_PREFIX)
    ]
    for state_key in breakout_keys:
        asset_name = state_key[len(BREAKOUT_STATE_KEY_PREFIX):]
        cfg = BREAKOUT_ASSETS.get(asset_name)
        if not cfg:
            continue
        try:
            raw = executor.get_klines(cfg["symbol"], cfg["interval"], 100)
            df = _build_dataframe(raw)
            df = _compute_indicators(df, cfg)
            pos = state["positions"][state_key]
            reason, kind = check_breakout_exit(
                df,
                position_direction=pos.get("direction", "LONG"),
                entry_price=float(pos["entry_price"]),
                atr_at_entry=float(pos["atr_at_entry"]),
                current_adx=float(df.iloc[-1].get("adx", 0) or 0),
                cfg=cfg,
            )
            if reason:
                logger.info("[%s] exit %s — closing", asset_name, reason)
                close_breakout_position(executor, state, state_key, reason)
        except Exception as e:
            logger.error("[%s] exit-management cycle errored: %s",
                         asset_name, e, exc_info=True)

    # 2. Pause flag short-circuits new entries (but exits already ran above)
    if BREAKOUT_PAUSED:
        save_state(state, owner="breakout")
        return

    # 3. Capacity check — global slot cap honored
    if _count_open_breakouts(state) >= MAX_BREAKOUT_POSITIONS:
        save_state(state, owner="breakout")
        return

    # 4. New entries
    for asset_name, cfg in BREAKOUT_ASSETS.items():
        state_key = f"{BREAKOUT_STATE_KEY_PREFIX}{asset_name}"
        if state_key in state.get("positions", {}):
            continue  # already open
        try:
            raw = executor.get_klines(cfg["symbol"], cfg["interval"], 100)
            df = _build_dataframe(raw)
            df = _compute_indicators(df, cfg)
            sig = analyze_breakout_entry(df, cfg)
            if sig["would_enter"]:
                open_breakout_position(
                    executor, state, asset_name, cfg, df, sig["direction"])
        except Exception as e:
            logger.error("[%s] entry cycle errored: %s",
                         asset_name, e, exc_info=True)

    save_state(state, owner="breakout")


def run() -> None:
    """Daemon entrypoint — loops forever with poll interval."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Breakout bot starting. PAUSED=%s", BREAKOUT_PAUSED)

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
        sleep_for = max(1.0, BREAKOUT_POLL_INTERVAL_SECONDS - elapsed)
        logger.info("Cycle %d done in %.1fs. Sleeping %.0fs.", cycle, elapsed, sleep_for)
        time.sleep(sleep_for)


if __name__ == "__main__":
    run()
