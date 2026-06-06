"""Phase I — RSI-VWAP Extreme Reversal bot main loop.

Polls each asset in REVERSAL_ASSETS, fetches klines, computes RSI(VWAP)
+ extreme-bar detector + dot polarity, and acts on entries/exits.

Mean-reversion strategy — catches 3× range capitulation candles at RSI
extremes (per Alex Carter Trading spec). Defaults to PAUSED until
backtest + paper validation.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from reversal_config import (
    REVERSAL_PAUSED, REVERSAL_POLL_INTERVAL_SECONDS,
    REVERSAL_STATE_KEY_PREFIX, REVERSAL_STRATEGY_TAG,
    REVERSAL_HEARTBEAT_FILE, REVERSAL_MARGIN_PER_TRADE, REVERSAL_LEVERAGE,
    MAX_REVERSAL_POSITIONS, REVERSAL_ASSETS,
)
from reversal_signals import (
    compute_rsi_vwap, analyze_reversal_entry,
)
from executor import Executor
from journal import log_trade
from position_manager import (
    load_state, save_state, register_entry, register_exit,
)

logger = logging.getLogger("crypto_bot.reversal_main")
_HEARTBEAT_FILE = REVERSAL_HEARTBEAT_FILE


def _write_heartbeat(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except Exception as e:
        logger.warning("Failed to write heartbeat: %s", e)


def _build_dataframe(raw_klines: list) -> pd.DataFrame:
    """Convert WEEX positional klines to OHLCV DataFrame via signals helper."""
    from signals import build_dataframe
    if not raw_klines:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    return build_dataframe(raw_klines).reset_index(drop=True)


def _compute_atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Wilder's ATR computed inline to avoid a pandas_ta dependency here."""
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close  = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / length, adjust=False).mean()


def open_reversal_position(
    executor: Executor, state: dict, asset_name: str, cfg: dict,
    df: pd.DataFrame, direction: str,
) -> None:
    symbol = cfg["symbol"]
    current_price = float(df.iloc[-1]["close"])
    atr = _compute_atr(df, length=cfg.get("atr_length", 14))
    atr_at_entry = float(atr.iloc[-1])
    if atr_at_entry <= 0:
        logger.warning("[%s] ATR=0; skipping entry", asset_name)
        return

    notional = REVERSAL_MARGIN_PER_TRADE * REVERSAL_LEVERAGE
    qty = round(notional / current_price, 4)
    if qty <= 0:
        return

    sl_mult = cfg.get("sl_atr_mult", 1.5)
    if direction == "SHORT":
        sl_price = current_price + sl_mult * atr_at_entry
    else:
        sl_price = current_price - sl_mult * atr_at_entry
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

    state_key = f"{REVERSAL_STATE_KEY_PREFIX}{asset_name}"
    register_entry(
        state, state_key,
        entry_price=current_price, atr_at_entry=atr_at_entry,
        quantity=qty,
        strategy=cfg.get("strategy_name", REVERSAL_STRATEGY_TAG),
        entry_reason=f"RSI-VWAP reversal {direction}",
        symbol=symbol, direction=direction, bars_held=0,
    )


def close_reversal_position(
    executor: Executor, state: dict, state_key: str, reason: str,
) -> None:
    pos = state.get("positions", {}).get(state_key)
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
            leverage=REVERSAL_LEVERAGE,
            strategy=pos.get("strategy", REVERSAL_STRATEGY_TAG),
            entry_reason=pos.get("entry_reason", ""),
            exit_reason=reason,
            date_closed=datetime.now(timezone.utc),
        )
    except Exception as e:
        logger.error("[%s] log_trade failed: %s", state_key, e)


def _check_exit(df: pd.DataFrame, pos: dict, cfg: dict) -> str | None:
    """Sensible-default exit rules per plan I.3 (source did not specify exits).

    SL: 1.5×ATR adverse, TP1: 1×ATR retrace, time stop: max_hold_bars.
    """
    direction = pos.get("direction", "LONG")
    entry_price = float(pos["entry_price"])
    atr_at_entry = float(pos.get("atr_at_entry", 0) or 0)
    if atr_at_entry <= 0:
        return None
    current_price = float(df.iloc[-1]["close"])
    bars_held = int(pos.get("bars_held", 0))

    sl_mult = cfg.get("sl_atr_mult", 1.5)
    tp1_mult = cfg.get("tp1_atr_mult", 1.0)

    is_long = direction == "LONG"
    if is_long:
        if current_price <= entry_price - sl_mult * atr_at_entry:
            return "SL Hit"
        if current_price >= entry_price + tp1_mult * atr_at_entry:
            return "TP1 Hit"
    else:
        if current_price >= entry_price + sl_mult * atr_at_entry:
            return "SL Hit"
        if current_price <= entry_price - tp1_mult * atr_at_entry:
            return "TP1 Hit"

    if bars_held >= cfg.get("max_hold_bars", 24):
        return "Time Stop"

    return None


def _count_open_reversals(state: dict) -> int:
    return sum(1 for k in state.get("positions", {})
               if k.startswith(REVERSAL_STATE_KEY_PREFIX))


def run_cycle(executor: Executor, state: dict) -> None:
    _write_heartbeat(_HEARTBEAT_FILE)

    # 1. Exit-management
    rev_keys = [k for k in list(state.get("positions", {}).keys())
                if k.startswith(REVERSAL_STATE_KEY_PREFIX)]
    for state_key in rev_keys:
        asset_name = state_key[len(REVERSAL_STATE_KEY_PREFIX):]
        cfg = REVERSAL_ASSETS.get(asset_name)
        if not cfg:
            continue
        try:
            raw = executor.get_klines(cfg["symbol"], cfg["interval"], 100)
            df = _build_dataframe(raw)
            pos = state["positions"][state_key]
            reason = _check_exit(df, pos, cfg)
            if reason:
                close_reversal_position(executor, state, state_key, reason)
            else:
                pos["bars_held"] = int(pos.get("bars_held", 0)) + 1
        except Exception as e:
            logger.error("[%s] exit cycle errored: %s", asset_name, e)

    # 2. Pause flag
    if REVERSAL_PAUSED:
        save_state(state, owner="reversal")
        return

    # 3. Capacity check
    if _count_open_reversals(state) >= MAX_REVERSAL_POSITIONS:
        save_state(state, owner="reversal")
        return

    # 4. Entry scan
    for asset_name, cfg in REVERSAL_ASSETS.items():
        state_key = f"{REVERSAL_STATE_KEY_PREFIX}{asset_name}"
        if state_key in state.get("positions", {}):
            continue
        try:
            raw = executor.get_klines(cfg["symbol"], cfg["interval"], 100)
            df = _build_dataframe(raw)
            sig = analyze_reversal_entry(df, cfg)
            if sig["would_enter"]:
                open_reversal_position(
                    executor, state, asset_name, cfg, df, sig["direction"])
        except Exception as e:
            logger.error("[%s] entry cycle errored: %s", asset_name, e)

    save_state(state, owner="reversal")


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Reversal bot starting. PAUSED=%s", REVERSAL_PAUSED)

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
        sleep_for = max(1.0, REVERSAL_POLL_INTERVAL_SECONDS - elapsed)
        logger.info("Cycle %d done in %.1fs. Sleeping %.0fs.", cycle, elapsed, sleep_for)
        time.sleep(sleep_for)


if __name__ == "__main__":
    run()
