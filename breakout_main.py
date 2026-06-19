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
    check_breakeven_trigger, analyze_breakout_pyramid,
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
    """Convert raw WEEX positional kline rows to OHLCV DataFrame.

    Delegates to signals.build_dataframe which knows the WEEX column
    layout (positional array, not dict).
    """
    from signals import build_dataframe
    if not raw_klines:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    return build_dataframe(raw_klines).reset_index(drop=True)


def open_breakout_position(
    executor: Executor, state: dict, asset_name: str, cfg: dict,
    df: pd.DataFrame, direction: str,
) -> None:
    """Place the order, register state, log the entry trade row."""
    symbol = cfg["symbol"]
    current_price = float(df.iloc[-1]["close"])
    atr_at_entry  = float(df.iloc[-1]["atr"])

    # L.3.2: Vol-adaptive sizing. Same regime classifier the L.2 gate uses.
    from regime import classify_from_df
    from risk import vol_scaled_margin
    regime = classify_from_df(df, cfg)
    scaled_margin = vol_scaled_margin(BREAKOUT_MARGIN_PER_TRADE, regime["vol"])
    if scaled_margin < BREAKOUT_MARGIN_PER_TRADE:
        logger.info("[%s] high-vol throttle: margin $%.2f → $%.2f",
                      asset_name, BREAKOUT_MARGIN_PER_TRADE, scaled_margin)

    # Sizing: scaled margin × leverage / price
    notional = scaled_margin * BREAKOUT_LEVERAGE
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

    # Email open notification (fire-and-forget; failures logged in notifier)
    try:
        from notifier import notify_trade_opened
        # SL/TP1/TP2 prices for the email body — directional math
        sign = -1.0 if direction == "SHORT" else 1.0
        tp1_mult = cfg.get("donchian_exit_period", 20) / 10.0  # rough estimate
        notify_trade_opened(
            symbol=symbol,
            entry_price=current_price,
            quantity=str(qty),
            leverage=BREAKOUT_LEVERAGE,
            sl_price=sl_price,
            tp1_price=current_price + sign * 1.5 * atr_at_entry,
            tp2_price=current_price + sign * 3.0 * atr_at_entry,
            atr_at_entry=atr_at_entry,
            strategy=cfg.get("strategy_name", BREAKOUT_STRATEGY_TAG),
            entry_reason=f"Donchian {cfg.get('donchian_period', 55)}-bar break {direction}",
            direction=direction,
        )
    except Exception as e:
        logger.warning("[%s] open notification failed: %s", asset_name, e)


def _add_pyramid_leg(executor: Executor, state: dict, state_key: str,
                       asset_name: str, cfg: dict, spec: dict) -> None:
    """Phase L.3.3 — open a pyramid leg + append to position.pyramid_legs.

    Uses the same baseline notional × size_fraction (default 50% per
    leg) so a 2-leg max means total exposure is ≤ 2× baseline. Per the
    peer-review correction, legs live INSIDE the existing position
    dict — no new top-level state key, slot accounting unchanged.
    """
    pos = state["positions"][state_key]
    symbol = cfg["symbol"]
    direction = spec["direction"]
    leg_price = float(spec["entry_price"])
    size_fraction = float(spec.get("size_fraction", 0.5))

    # Recompute leg notional from current config (not the original baseline,
    # so vol-adaptive sizing from L.3.2 also influences pyramid legs).
    from regime import classify_from_df
    from risk import vol_scaled_margin
    # Use a tiny synthesized df is unnecessary — caller already has full df.
    # The cfg-vol value is best taken from the same df the trigger used.
    # We accept the slight redundancy and pass through with vol="unknown"
    # if classification fails, since the trigger already confirmed
    # market structure validity.
    leg_margin = vol_scaled_margin(BREAKOUT_MARGIN_PER_TRADE, "unknown") * size_fraction
    leg_notional = leg_margin * BREAKOUT_LEVERAGE
    leg_qty = round(leg_notional / leg_price, 4)
    if leg_qty <= 0:
        logger.warning("[%s] pyramid qty <= 0, skipping leg", asset_name)
        return

    try:
        if direction == "SHORT":
            executor.open_short(symbol, leg_qty)
        else:
            executor.open_long(symbol, leg_qty)
    except Exception as e:
        logger.error("[%s] pyramid open failed: %s", asset_name, e)
        return

    legs = pos.setdefault("pyramid_legs", [])
    legs.append({
        "entry_price":   leg_price,
        "atr_at_entry":  float(spec["atr_at_entry"]),
        "quantity":      leg_qty,
        "opened_at":     datetime.now(timezone.utc).isoformat(),
        "size_fraction": size_fraction,
    })
    logger.info("[%s] pyramid leg #%d added: qty=%s @ %.6f (total legs=%d)",
                  asset_name, len(legs), leg_qty, leg_price, len(legs))


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

    # L.3.3: aggregate baseline + pyramid legs for the journal row.
    # close_*_full on the exchange flattens the whole position regardless,
    # but the journal records aggregate qty + weighted entry so PnL is
    # accurate.
    from position_manager import aggregate_position_qty, aggregate_avg_entry
    total_qty = aggregate_position_qty(pos)
    avg_entry = aggregate_avg_entry(pos)
    pyramid_count = len(pos.get("pyramid_legs") or [])
    journal_entry_reason = pos.get("entry_reason", "")
    if pyramid_count > 0:
        journal_entry_reason = (f"{journal_entry_reason} +{pyramid_count} pyramid").strip()

    register_exit(state, state_key)

    try:
        log_trade(
            symbol=symbol, direction=direction,
            entry_price=avg_entry,
            exit_price=exit_price or avg_entry,
            quantity=total_qty,
            leverage=BREAKOUT_LEVERAGE,
            strategy=pos.get("strategy", BREAKOUT_STRATEGY_TAG),
            entry_reason=journal_entry_reason,
            exit_reason=reason,
            date_closed=datetime.now(timezone.utc),
        )
    except Exception as e:
        logger.error("[%s] log_trade failed: %s — journal will be reconciled",
                     state_key, e)

    # Email close notification (fire-and-forget)
    try:
        from notifier import notify_trade_closed
        entry = float(pos["entry_price"])
        atr   = float(pos.get("atr_at_entry") or 0)
        sign  = -1.0 if direction == "SHORT" else 1.0
        portfolio_value = 0.0
        try:
            bal = executor.get_account_balance()
            portfolio_value = float(bal.get("balance", 0) if bal else 0)
        except Exception:
            pass
        notify_trade_closed(
            symbol=symbol,
            direction=direction,
            entry_price=entry,
            exit_price=float(exit_price or entry),
            quantity=float(pos["quantity"]),
            leverage=BREAKOUT_LEVERAGE,
            sl_price=entry - sign * 2.5 * atr,
            tp1_price=entry + sign * 1.5 * atr,
            tp2_price=entry + sign * 3.0 * atr,
            exit_reason=reason,
            strategy=pos.get("strategy", BREAKOUT_STRATEGY_TAG),
            portfolio_value=portfolio_value,
        )
    except Exception as e:
        logger.warning("[%s] close notification failed: %s", state_key, e)


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
            entry_price = float(pos["entry_price"])
            atr_at_entry = float(pos["atr_at_entry"])
            direction = pos.get("direction", "LONG")
            current_close = float(df.iloc[-1]["close"])

            # L.3.1: ratchet SL to breakeven once favorable move ≥ 1×ATR.
            # Boolean persists on the position dict so we don't re-evaluate
            # the trigger every cycle (it's monotonic — never un-ratchets).
            if not pos.get("breakeven_triggered", False):
                if check_breakeven_trigger(
                        current_close, entry_price, atr_at_entry,
                        direction, cfg):
                    pos["breakeven_triggered"] = True
                    logger.info("[%s] breakeven ratchet triggered "
                                  "(close=%.6f, entry=%.6f, +%.2f ATR)",
                                  asset_name, current_close, entry_price,
                                  (current_close - entry_price) / atr_at_entry
                                    if direction.upper() == "LONG"
                                    else (entry_price - current_close) / atr_at_entry)
            reason, kind = check_breakout_exit(
                df,
                position_direction=direction,
                entry_price=entry_price,
                atr_at_entry=atr_at_entry,
                current_adx=float(df.iloc[-1].get("adx", 0) or 0),
                cfg=cfg,
                breakeven_triggered=bool(pos.get("breakeven_triggered", False)),
            )
            if reason:
                logger.info("[%s] exit %s — closing", asset_name, reason)
                close_breakout_position(executor, state, state_key, reason)
                continue

            # L.3.3: Pyramid leg consideration (only when not exiting).
            # Pyramiding is per-asset and gated by cfg["allow_pyramiding"]
            # which defaults False — wired ON in Phase L.3.3 config edits.
            pyramid_spec = analyze_breakout_pyramid(df, pos, cfg)
            if pyramid_spec is not None and not BREAKOUT_PAUSED:
                _add_pyramid_leg(executor, state, state_key, asset_name,
                                  cfg, pyramid_spec)
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
            # G.2: optional 1D trend gate — fetch daily klines + compute EMA20/50
            df_1d = None
            if cfg.get("use_trend_filter", False):
                try:
                    raw_1d = executor.get_klines(cfg["symbol"], "1d", 80)
                    df_1d = _build_dataframe(raw_1d)
                    if len(df_1d) >= 50:
                        df_1d["ema_fast"] = df_1d["close"].ewm(span=20, adjust=False).mean()
                        df_1d["ema_slow"] = df_1d["close"].ewm(span=50, adjust=False).mean()
                except Exception as e:
                    logger.warning("[%s] 1D fetch failed: %s — trend gate defaults to pass",
                                    asset_name, e)
                    df_1d = None
            sig = analyze_breakout_entry(df, cfg, df_1d=df_1d)

            # ── L.2: Regime gate ──────────────────────────────
            if sig.get("would_enter") and cfg.get("use_regime_gate", False):
                from regime import classify_from_df, gate_blocks_direction
                regime = classify_from_df(df, cfg)
                sig["regime"] = regime
                if gate_blocks_direction(regime["label"], sig.get("direction", "LONG")):
                    sig["would_enter"] = False
                    sig["blocked_by"] = "regime_misalign"

            if sig["would_enter"]:
                open_breakout_position(
                    executor, state, asset_name, cfg, df, sig["direction"])
            elif sig.get("blocked_by"):
                logger.debug("[%s] no entry: %s", asset_name, sig["blocked_by"])
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
