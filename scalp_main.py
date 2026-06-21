"""Phase M — Scalp bot main loop.

Polls each asset in SCALP_ASSETS every minute. On each freshly-closed
5m bar, runs analyze_scalp_entry against the volatility-expansion +
momentum + new-N-bar-high + body-color gates. Opens positions via the
existing executor + journal + state-manager plumbing.

State prefix: SCALP_. position_manager.BOT_PREFIXES extended to
recognize the new prefix for slot accounting.
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from scalp_config import (
    SCALP_PAUSED, SCALP_POLL_INTERVAL_SECONDS,
    SCALP_STATE_KEY_PREFIX, SCALP_STRATEGY_TAG,
    SCALP_HEARTBEAT_FILE, SCALP_MARGIN_PER_TRADE, SCALP_LEVERAGE,
    MAX_SCALP_POSITIONS, SCALP_ASSETS, SCALP_COOLDOWN_SECONDS,
)
from scalp_signals import analyze_scalp_entry, check_scalp_exit
from executor import Executor
from journal import log_trade
from position_manager import (
    load_state, save_state, register_entry, register_exit,
)

logger = logging.getLogger("crypto_bot.scalp_main")
_HEARTBEAT_FILE = SCALP_HEARTBEAT_FILE


def _write_heartbeat(path: Path) -> None:
    """Touch the heartbeat file so the dashboard shows scalp as LIVE."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to write heartbeat: %s", e)


def _build_dataframe(raw_klines: list) -> pd.DataFrame:
    """Convert raw WEEX positional kline rows to OHLCV DataFrame."""
    from signals import build_dataframe
    if not raw_klines:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    return build_dataframe(raw_klines).reset_index(drop=True)


# ─── Cooldown helpers ──────────────────────────────────────────────────

def _cooldown_map(state: dict) -> dict:
    return state.setdefault("scalp_cooldowns", {})


def _on_cooldown(state: dict, symbol: str) -> bool:
    """Is `symbol` still under the post-exit re-entry block?"""
    iso = _cooldown_map(state).get(symbol)
    if not iso:
        return False
    try:
        exit_ts = datetime.fromisoformat(iso)
        if exit_ts.tzinfo is None:
            exit_ts = exit_ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    age = (datetime.now(timezone.utc) - exit_ts).total_seconds()
    return age < SCALP_COOLDOWN_SECONDS


def _record_exit_for_cooldown(state: dict, symbol: str) -> None:
    _cooldown_map(state)[symbol] = datetime.now(timezone.utc).isoformat()


# ─── Open / Close ──────────────────────────────────────────────────────

def open_scalp_position(executor: Executor, state: dict, asset_name: str,
                          cfg: dict, df: pd.DataFrame, direction: str) -> None:
    """Place the order, register state, log + notify the entry."""
    symbol = cfg["symbol"]
    current_price = float(df.iloc[-1]["close"])
    # Scalp uses pct brackets, not ATR; still capture ATR (range proxy)
    # for the journal + dashboard parity with the other bots.
    range_ = float(df.iloc[-1]["high"] - df.iloc[-1]["low"])

    # L.3.2: vol-adaptive sizing (same helper the other bots use)
    from regime import classify_from_df
    from risk import vol_scaled_margin
    regime = classify_from_df(df, cfg)
    scaled_margin = vol_scaled_margin(SCALP_MARGIN_PER_TRADE, regime["vol"])
    if scaled_margin < SCALP_MARGIN_PER_TRADE:
        logger.info("[%s] high-vol throttle: margin $%.2f → $%.2f",
                      asset_name, SCALP_MARGIN_PER_TRADE, scaled_margin)

    notional = scaled_margin * SCALP_LEVERAGE
    qty = round(notional / current_price, 4)
    if qty <= 0:
        logger.warning("[%s] computed qty <= 0; skipping", asset_name)
        return

    # Pre-compute SL/TP prices for notification body (the actual exit
    # decision lives in check_scalp_exit at every poll).
    sl_pct = float(cfg.get("sl_pct", 1.5))
    tp_pct = float(cfg.get("tp_pct", 3.0))
    if direction == "LONG":
        sl_price = current_price * (1 - sl_pct / 100)
        tp_price = current_price * (1 + tp_pct / 100)
    else:
        sl_price = current_price * (1 + sl_pct / 100)
        tp_price = current_price * (1 - tp_pct / 100)
    sl_str = f"{sl_price:.6f}"

    logger.info("[%s] OPENING %s qty=%s price=%.4f SL=%s TP=%.6f range=%.4f",
                  asset_name, direction, qty, current_price, sl_str,
                  tp_price, range_)

    try:
        if direction == "SHORT":
            executor.open_short(symbol, qty, sl_trigger_price=sl_str)
        else:
            executor.open_long(symbol, qty, sl_trigger_price=sl_str)
    except Exception as e:  # noqa: BLE001
        logger.error("[%s] exchange open failed: %s", asset_name, e)
        return

    state_key = f"{SCALP_STATE_KEY_PREFIX}{asset_name}"
    register_entry(
        state, state_key,
        entry_price=current_price,
        atr_at_entry=range_,  # store range as ATR-proxy for dashboard parity
        quantity=qty,
        strategy=cfg.get("strategy_name", SCALP_STRATEGY_TAG),
        entry_reason=f"vol-expand + 20-bar momentum + new {cfg.get('new_high_lookback', 20)}-bar {'high' if direction == 'LONG' else 'low'}",
        symbol=symbol,
        direction=direction,
    )

    # Discord + email notification
    try:
        from notifier import notify_trade_opened
        notify_trade_opened(
            symbol=symbol,
            entry_price=current_price,
            quantity=str(qty),
            leverage=SCALP_LEVERAGE,
            sl_price=sl_price,
            tp1_price=tp_price,
            tp2_price=tp_price,
            atr_at_entry=range_,
            strategy=cfg.get("strategy_name", SCALP_STRATEGY_TAG),
            entry_reason=f"Vol-expansion + 20-bar new {'high' if direction == 'LONG' else 'low'} {direction}",
            direction=direction,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] open notification failed: %s", asset_name, e)


def close_scalp_position(executor: Executor, state: dict, state_key: str,
                            reason: str) -> None:
    """Close on exchange, strip state, write journal row, send notification."""
    pos = state.get("positions", {}).get(state_key)
    if not pos:
        logger.warning("close_scalp_position called for missing key %s", state_key)
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
    except Exception as e:  # noqa: BLE001
        logger.error("[%s] exchange close failed: %s — state will retry next cycle",
                      state_key, e)
        return

    register_exit(state, state_key)
    _record_exit_for_cooldown(state, symbol)

    try:
        log_trade(
            symbol=symbol, direction=direction,
            entry_price=pos["entry_price"],
            exit_price=exit_price or pos["entry_price"],
            quantity=float(pos["quantity"]),
            leverage=SCALP_LEVERAGE,
            strategy=pos.get("strategy", SCALP_STRATEGY_TAG),
            entry_reason=pos.get("entry_reason", ""),
            exit_reason=reason,
            date_closed=datetime.now(timezone.utc),
        )
    except Exception as e:  # noqa: BLE001
        logger.error("[%s] log_trade failed: %s — journal will be reconciled",
                      state_key, e)

    try:
        from notifier import notify_trade_closed
        entry = float(pos["entry_price"])
        range_at_entry = float(pos.get("atr_at_entry") or 0)
        sign = -1.0 if direction == "SHORT" else 1.0
        portfolio_value = 0.0
        try:
            bal = executor.get_account_balance()
            portfolio_value = float(bal.get("balance", 0) if bal else 0)
        except Exception:  # noqa: BLE001
            pass
        notify_trade_closed(
            symbol=symbol, direction=direction,
            entry_price=entry,
            exit_price=exit_price or entry,
            quantity=float(pos["quantity"]),
            leverage=SCALP_LEVERAGE,
            sl_price=entry + sign * (1.5 / 100) * entry * -1 if direction == "LONG"
                       else entry + sign * (1.5 / 100) * entry,
            tp1_price=entry + sign * (3.0 / 100) * entry,
            tp2_price=entry + sign * (3.0 / 100) * entry,
            exit_reason=reason,
            strategy=pos.get("strategy", SCALP_STRATEGY_TAG),
            portfolio_value=portfolio_value,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] close notification failed: %s", state_key, e)


# ─── Cycle ─────────────────────────────────────────────────────────────

def _count_open_scalps(state: dict) -> int:
    return sum(1 for k in state.get("positions", {})
                if k.startswith(SCALP_STATE_KEY_PREFIX))


def run_cycle(executor: Executor, state: dict) -> None:
    """One full poll: heartbeat → exits on open positions → entries on signals."""
    _write_heartbeat(_HEARTBEAT_FILE)

    # 1. Manage exits on existing scalp positions
    scalp_keys = [
        k for k in list(state.get("positions", {}).keys())
        if k.startswith(SCALP_STATE_KEY_PREFIX)
    ]
    for state_key in scalp_keys:
        asset_name = state_key[len(SCALP_STATE_KEY_PREFIX):]
        cfg = SCALP_ASSETS.get(asset_name)
        if not cfg:
            continue
        try:
            pos = state["positions"][state_key]
            symbol = pos.get("symbol", cfg["symbol"])
            entry_price = float(pos["entry_price"])
            direction = pos.get("direction", "LONG")
            # Fetch the current mark price — exit is pct-bracket, no DF needed
            current_price = executor.get_symbol_price(symbol)
            if not current_price:
                continue
            reason = check_scalp_exit(entry_price, float(current_price),
                                         direction, cfg)
            if reason:
                logger.info("[%s] exit %s — closing", asset_name, reason)
                close_scalp_position(executor, state, state_key, reason)
        except Exception as e:  # noqa: BLE001
            logger.error("[%s] exit-management cycle errored: %s",
                          asset_name, e, exc_info=True)

    # 2. Pause flag short-circuits new entries (but exits already ran above)
    if SCALP_PAUSED:
        save_state(state, owner="scalp")
        return

    # 3. Capacity check — global slot cap honored
    if _count_open_scalps(state) >= MAX_SCALP_POSITIONS:
        save_state(state, owner="scalp")
        return

    # 4. Tier 0.1 — kill switch check (skip ALL new entries on streak/daily-DD trip)
    try:
        from kill_switch import should_pause
        ks = should_pause("scalp")
        if ks.paused:
            logger.warning("Kill-switch active for scalp: %s — skipping all new entries",
                            ks.reason)
            save_state(state, owner="scalp")
            return
    except Exception as e:  # noqa: BLE001
        logger.warning("Kill-switch check failed (allowing entries): %s", e)

    # 5. New entries
    for asset_name, cfg in SCALP_ASSETS.items():
        state_key = f"{SCALP_STATE_KEY_PREFIX}{asset_name}"
        if state_key in state.get("positions", {}):
            continue  # already open
        # Cooldown check — block re-entry on this symbol for N seconds after exit
        if _on_cooldown(state, cfg["symbol"]):
            continue
        try:
            raw = executor.get_klines(cfg["symbol"], cfg["interval"], 100)
            df = _build_dataframe(raw)
            if df is None or len(df) < 60:
                continue

            # Phase M.2: fetch 1h klines for higher-TF trend gate.
            # Mirror of breakout_main's 1D fetch pattern (graceful
            # degradation — None means the gate inside analyze_scalp_entry
            # defaults to pass).
            df_1h = None
            if cfg.get("use_higher_tf_trend", False):
                try:
                    raw_1h = executor.get_klines(
                        cfg["symbol"], cfg.get("higher_tf_interval", "1h"), 80)
                    df_1h = _build_dataframe(raw_1h)
                    if df_1h is not None and len(df_1h) >= 50:
                        ef = int(cfg.get("higher_tf_ema_fast", 20))
                        es = int(cfg.get("higher_tf_ema_slow", 50))
                        df_1h["ema_fast"] = df_1h["close"].ewm(span=ef, adjust=False).mean()
                        df_1h["ema_slow"] = df_1h["close"].ewm(span=es, adjust=False).mean()
                except Exception as e:  # noqa: BLE001
                    logger.warning("[%s] 1h fetch failed: %s — trend gate defaults to pass",
                                      asset_name, e)
                    df_1h = None
            sig = analyze_scalp_entry(df, cfg, df_1h=df_1h)

            # Observability: per-asset log + signal_status persistence
            def _sym(v):
                return "✅" if v is True else "❌" if v is False else "➖"
            filter_line = " ".join(
                f"{k}:{_sym(v)}" for k, v in (sig.get("filters") or {}).items()
                if v is not None
            )
            status_emoji = "🟢" if sig["would_enter"] else "⚪"
            logger.info("%s [%s] signal: would_enter=%s | blocked_by=%s | %s",
                          status_emoji, asset_name, sig["would_enter"],
                          sig.get("blocked_by") or "none", filter_line)
            if "signal_status" not in state:
                state["signal_status"] = {}
            state["signal_status"][asset_name] = {
                "symbol":        cfg["symbol"],
                "interval":      cfg["interval"],
                "strategy_name": cfg.get("strategy_name", asset_name),
                "checked_at":    datetime.now(timezone.utc).isoformat(),
                "would_enter":   sig.get("would_enter", False),
                "blocked_by":    sig.get("blocked_by"),
                "filters":       sig.get("filters", {}),
                "direction":     sig.get("direction"),
            }

            if sig["would_enter"]:
                open_scalp_position(
                    executor, state, asset_name, cfg, df, sig["direction"])
                # Stop after one new entry per cycle to respect MAX_SCALP_POSITIONS
                if _count_open_scalps(state) >= MAX_SCALP_POSITIONS:
                    break
        except Exception as e:  # noqa: BLE001
            logger.error("[%s] entry cycle errored: %s",
                          asset_name, e, exc_info=True)

    save_state(state, owner="scalp")


def run() -> None:
    """Daemon entrypoint — loops forever with poll interval."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Scalp bot starting. PAUSED=%s", SCALP_PAUSED)

    executor = Executor()
    state = load_state()

    cycle = 0
    while True:
        cycle += 1
        t0 = time.time()
        try:
            run_cycle(executor, state)
        except Exception as e:  # noqa: BLE001
            logger.error("Cycle %d errored: %s", cycle, e, exc_info=True)
        elapsed = time.time() - t0
        sleep_for = max(1, SCALP_POLL_INTERVAL_SECONDS - elapsed)
        logger.info("Cycle %d done in %.1fs. Sleeping %.0fs.", cycle, elapsed, sleep_for)
        time.sleep(sleep_for)


if __name__ == "__main__":
    run()
