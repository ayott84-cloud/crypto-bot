"""Phase N — Crossover bot main loop.

Polls each asset in CROSSOVER_ASSETS every minute. On each freshly-closed
5m bar, runs analyze_crossover_entry against the SMA(50)/SMA(100) cross.
Opens positions via the existing executor + journal + state-manager
plumbing. Identical operational shape to scalp_main.

State prefix: CROSSOVER_. position_manager.BOT_PREFIXES extended to
recognize the new prefix for slot accounting.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from crossover_config import (
    CROSSOVER_PAUSED, CROSSOVER_POLL_INTERVAL_SECONDS,
    CROSSOVER_STATE_KEY_PREFIX, CROSSOVER_STRATEGY_TAG,
    CROSSOVER_HEARTBEAT_FILE, CROSSOVER_MARGIN_PER_TRADE, CROSSOVER_LEVERAGE,
    MAX_CROSSOVER_POSITIONS, CROSSOVER_ASSETS, CROSSOVER_COOLDOWN_SECONDS,
)
from crossover_signals import analyze_crossover_entry, check_crossover_exit
from executor import Executor
from journal import log_trade
from position_manager import (
    load_state, save_state, register_entry, register_exit,
)

logger = logging.getLogger("crypto_bot.crossover_main")
_HEARTBEAT_FILE = CROSSOVER_HEARTBEAT_FILE


def _write_heartbeat(path: Path) -> None:
    """Touch the heartbeat file so the dashboard shows crossover as LIVE."""
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
    return state.setdefault("crossover_cooldowns", {})


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
    return age < CROSSOVER_COOLDOWN_SECONDS


def _record_exit_for_cooldown(state: dict, symbol: str) -> None:
    _cooldown_map(state)[symbol] = datetime.now(timezone.utc).isoformat()


def _compute_df_atr(df: pd.DataFrame, period: int = 14) -> float | None:
    """Wilder-style ATR from an OHLC DataFrame. None on insufficient data."""
    try:
        if df is None or len(df) < period + 2:
            return None
        high, low, close = df["high"], df["low"], df["close"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-2]
        return None if atr != atr else float(atr)
    except Exception:  # noqa: BLE001
        return None


# ─── Open / Close ──────────────────────────────────────────────────────

def open_crossover_position(executor: Executor, state: dict, asset_name: str,
                              cfg: dict, df: pd.DataFrame, direction: str) -> None:
    """Place the order, register state, log + notify the entry."""
    symbol = cfg["symbol"]
    current_price = float(df.iloc[-1]["close"])
    range_ = float(df.iloc[-1]["high"] - df.iloc[-1]["low"])

    # L.3.2: vol-adaptive sizing (mirrors scalp_main)
    from regime import classify_from_df
    from risk import vol_scaled_margin
    regime = classify_from_df(df, cfg)
    scaled_margin = vol_scaled_margin(CROSSOVER_MARGIN_PER_TRADE, regime["vol"])
    if scaled_margin < CROSSOVER_MARGIN_PER_TRADE:
        logger.info("[%s] high-vol throttle: margin $%.2f → $%.2f",
                      asset_name, CROSSOVER_MARGIN_PER_TRADE, scaled_margin)

    notional = scaled_margin * CROSSOVER_LEVERAGE
    qty = round(notional / current_price, 4)
    if qty <= 0:
        logger.warning("[%s] computed qty <= 0; skipping", asset_name)
        return

    # N.3 — compute real ATR(14) at entry for the emergency stop (the
    # bar range proxy is too noisy for a 3.5x multiple).
    atr_at_entry = _compute_df_atr(df, period=14) or range_

    invalidation_mode = cfg.get("exit_mode") == "invalidation"
    if invalidation_mode:
        # Primary exit is the SMA-recross (bot-side, close-confirmed).
        # Exchange-resident SL sits at the wide EMERGENCY level only;
        # no TP — the signal decides when the trend is done.
        mult = float(cfg.get("emergency_atr_mult", 3.5))
        if direction == "LONG":
            sl_price = current_price - mult * atr_at_entry
            tp_price = None
        else:
            sl_price = current_price + mult * atr_at_entry
            tp_price = None
    else:
        sl_pct = float(cfg.get("sl_pct", 1.0))
        tp_pct = float(cfg.get("tp_pct", 2.0))
        if direction == "LONG":
            sl_price = current_price * (1 - sl_pct / 100)
            tp_price = current_price * (1 + tp_pct / 100)
        else:
            sl_price = current_price * (1 + sl_pct / 100)
            tp_price = current_price * (1 - tp_pct / 100)
    sl_str = f"{sl_price:.6f}"

    fast_n = int(cfg.get("sma_fast", 50))
    slow_n = int(cfg.get("sma_slow", 100))
    logger.info("[%s] OPENING %s qty=%s price=%.4f SL=%s TP=%s "
                  "(SMA%d/%d cross, exit=%s)",
                  asset_name, direction, qty, current_price, sl_str,
                  f"{tp_price:.6f}" if tp_price else "signal-invalidation",
                  fast_n, slow_n,
                  "invalidation" if invalidation_mode else "bracket")

    # P1.2 — OCO hygiene: cancel stale triggers before the new bracketed
    # entry so brackets never stack. Cancel failure must not block entry.
    try:
        executor.cancel_pending_orders(symbol)
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] pre-entry trigger cancel failed (continuing): %s",
                        asset_name, e)

    # P1.1 — attach bracket legs at entry (exchange-enforced). In
    # invalidation mode only the emergency SL rides the entry (no TP).
    tp_str = f"{tp_price:.6f}" if tp_price else None
    try:
        if direction == "SHORT":
            executor.open_short(symbol, qty, sl_trigger_price=sl_str,
                                  tp_trigger_price=tp_str)
        else:
            executor.open_long(symbol, qty, sl_trigger_price=sl_str,
                                 tp_trigger_price=tp_str)
    except Exception as e:  # noqa: BLE001
        logger.error("[%s] exchange open failed: %s", asset_name, e)
        return

    state_key = f"{CROSSOVER_STATE_KEY_PREFIX}{asset_name}"
    entry_reason = f"SMA{fast_n}/SMA{slow_n} {'golden' if direction == 'LONG' else 'death'} cross"
    register_entry(
        state, state_key,
        entry_price=current_price,
        atr_at_entry=atr_at_entry,
        quantity=qty,
        strategy=cfg.get("strategy_name", CROSSOVER_STRATEGY_TAG),
        entry_reason=entry_reason,
        symbol=symbol,
        direction=direction,
        # P5 findings 3+9 — persist exit semantics + triggers ON the
        # position: the exit path routes by exit_kind (migration guard —
        # pre-N.3 positions keep their pct bracket), and notifications
        # report the actual exchange-resident prices.
        exit_kind="invalidation" if invalidation_mode else "bracket",
        sl_price=sl_price,
        tp_price=tp_price,
    )

    try:
        from notifier import notify_trade_opened
        # tp_price=None in invalidation mode renders as "—" (no TP by
        # design — the SMA-recross is the profit mechanism).
        notify_trade_opened(
            symbol=symbol,
            entry_price=current_price,
            quantity=str(qty),
            leverage=CROSSOVER_LEVERAGE,
            sl_price=sl_price,
            tp1_price=tp_price,
            tp2_price=tp_price,
            atr_at_entry=atr_at_entry,
            strategy=cfg.get("strategy_name", CROSSOVER_STRATEGY_TAG),
            entry_reason=entry_reason,
            direction=direction,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] open notification failed: %s", asset_name, e)


def _position_uses_invalidation_exit(pos: dict) -> bool:
    """P5 finding 3 — deploy-migration guard. Only positions OPENED by
    N.3 code (exit_kind='invalidation') use the v3 invalidation exit:
    pre-N.3 positions stored atr_at_entry as a bar-range proxy, so the
    3.5xATR emergency stop would sit at a near-zero (or missing)
    distance while their real exchange bracket is the pct SL they
    registered at entry."""
    return pos.get("exit_kind") == "invalidation"


def close_crossover_position(executor: Executor, state: dict, state_key: str,
                                reason: str,
                                exit_price_override: float | None = None) -> None:
    """Close on exchange, strip state, write journal row, send notification.

    exit_price_override (P1.1): bracket exits pass the trigger price so
    paper fills model the exchange-resident order, not the polled price.
    """
    pos = state.get("positions", {}).get(state_key)
    if not pos:
        logger.warning("close_crossover_position called for missing key %s", state_key)
        return

    symbol = pos.get("symbol", "")
    direction = pos.get("direction", "LONG")
    exit_price = (exit_price_override
                   if exit_price_override is not None
                   else executor.get_symbol_price(symbol) or pos.get("entry_price"))

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
            leverage=CROSSOVER_LEVERAGE,
            strategy=pos.get("strategy", CROSSOVER_STRATEGY_TAG),
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
        portfolio_value = 0.0
        try:
            bal = executor.get_account_balance()
            portfolio_value = float(bal.get("balance", 0) if bal else 0)
        except Exception:  # noqa: BLE001
            pass
        # P5 finding 9 — report the bracket the position ACTUALLY ran
        # (persisted at entry); legacy positions without the fields
        # render as "—" instead of fabricated percentages.
        notify_trade_closed(
            symbol=symbol, direction=direction,
            entry_price=entry,
            exit_price=exit_price or entry,
            quantity=float(pos["quantity"]),
            leverage=CROSSOVER_LEVERAGE,
            sl_price=pos.get("sl_price"),
            tp1_price=pos.get("tp_price"),
            tp2_price=pos.get("tp_price"),
            exit_reason=reason,
            strategy=pos.get("strategy", CROSSOVER_STRATEGY_TAG),
            portfolio_value=portfolio_value,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] close notification failed: %s", state_key, e)


# ─── Cycle ─────────────────────────────────────────────────────────────

def _count_open_crossovers(state: dict) -> int:
    return sum(1 for k in state.get("positions", {})
                if k.startswith(CROSSOVER_STATE_KEY_PREFIX))


def run_cycle(executor: Executor, state: dict) -> None:
    """One full poll: heartbeat → exits on open positions → entries on signals."""
    _write_heartbeat(_HEARTBEAT_FILE)

    # 1. Manage exits on existing crossover positions
    crossover_keys = [
        k for k in list(state.get("positions", {}).keys())
        if k.startswith(CROSSOVER_STATE_KEY_PREFIX)
    ]
    for state_key in crossover_keys:
        asset_name = state_key[len(CROSSOVER_STATE_KEY_PREFIX):]
        cfg = CROSSOVER_ASSETS.get(asset_name)
        if not cfg:
            continue
        try:
            pos = state["positions"][state_key]
            symbol = pos.get("symbol", cfg["symbol"])
            entry_price = float(pos["entry_price"])
            direction = pos.get("direction", "LONG")
            current_price = executor.get_symbol_price(symbol)
            if not current_price:
                continue

            # P5 finding 3 — route by the exit semantics the position was
            # OPENED with, not live cfg: pre-N.3 positions keep the pct
            # bracket they registered on the exchange.
            if _position_uses_invalidation_exit(pos):
                # N.3 — primary exit is signal invalidation (close crossed
                # back through SMA-fast) + wide emergency ATR stop. Needs
                # klines for the SMA; fetch is cheap at 1h cadence.
                from crossover_signals import check_crossover_exit_v3
                sma_fast_now = None
                try:
                    raw = executor.get_klines(symbol, cfg["interval"], 60)
                    df_exit = _build_dataframe(raw)
                    fast_n = int(cfg.get("sma_fast", 20))
                    if df_exit is not None and len(df_exit) >= fast_n + 2:
                        sma_fast_now = float(
                            df_exit["close"].rolling(fast_n).mean().iloc[-2])
                        # Exit decision uses the last COMPLETED close, not
                        # the mid-bar tick — invalidation is close-confirmed
                        current_price = float(df_exit["close"].iloc[-2])
                except Exception as e:  # noqa: BLE001
                    logger.warning("[%s] exit klines fetch failed: %s",
                                    asset_name, e)
                atr_entry = float(pos.get("atr_at_entry") or 0)
                reason = check_crossover_exit_v3(
                    direction=direction, entry_price=entry_price,
                    current_close=float(current_price),
                    sma_fast_now=sma_fast_now,
                    atr_at_entry=atr_entry, cfg=cfg)
                if reason:
                    # Emergency SL fills at its trigger — the PERSISTED
                    # exchange-resident stop (P5 finding 9), so mid-position
                    # cfg tuning can't shift the modeled fill. Invalidation
                    # exits fill at the close that confirmed the recross.
                    override = None
                    if reason == "Emergency SL":
                        override = pos.get("sl_price")
                        if override is None and atr_entry > 0:
                            mult = float(cfg.get("emergency_atr_mult", 3.5))
                            override = (entry_price - mult * atr_entry
                                         if direction == "LONG"
                                         else entry_price + mult * atr_entry)
                    logger.info("[%s] exit %s — closing", asset_name, reason)
                    close_crossover_position(executor, state, state_key,
                                                reason,
                                                exit_price_override=override)
                continue

            reason = check_crossover_exit(entry_price, float(current_price),
                                            direction, cfg)
            if reason:
                # P1.1 — bracket exits fill at the exchange trigger price
                from risk import bracket_trigger_price
                trigger = bracket_trigger_price(
                    entry_price, direction, reason, cfg,
                    default_sl_pct=1.0, default_tp_pct=2.0)
                logger.info("[%s] exit %s — closing (fill=%s)",
                             asset_name, reason,
                             f"{trigger:.6f}" if trigger else "polled")
                close_crossover_position(executor, state, state_key, reason,
                                            exit_price_override=trigger)
        except Exception as e:  # noqa: BLE001
            logger.error("[%s] exit-management cycle errored: %s",
                          asset_name, e, exc_info=True)

    # 2. Pause flag short-circuits new entries
    if CROSSOVER_PAUSED:
        save_state(state, owner="crossover")
        return

    # 3. Capacity check
    if _count_open_crossovers(state) >= MAX_CROSSOVER_POSITIONS:
        save_state(state, owner="crossover")
        return

    # 4. Tier 0.1 — kill switch check (skip ALL new entries on streak/daily-DD trip)
    try:
        from kill_switch import should_pause
        ks = should_pause("crossover")
        if ks.paused:
            logger.warning("Kill-switch active for crossover: %s — skipping all new entries",
                            ks.reason)
            save_state(state, owner="crossover")
            return
    except Exception as e:  # noqa: BLE001
        logger.warning("Kill-switch check failed (allowing entries): %s", e)

    # 5. Tier 0.2 — sample current bid-ask spreads (one batch API call)
    # and update the rolling-spread tracker. Per-asset is_air_pocket
    # check is applied just before opening, below.
    try:
        from microstructure import get_default_tracker, fetch_all_spreads_bps
        spread_tracker = get_default_tracker()
        for sym, bps in fetch_all_spreads_bps(executor).items():
            spread_tracker.add_sample(sym, bps)
    except Exception as e:  # noqa: BLE001
        logger.debug("Spread sample fetch failed (gate will degrade to allow): %s", e)
        spread_tracker = None

    # 6. New entries
    for asset_name, cfg in CROSSOVER_ASSETS.items():
        state_key = f"{CROSSOVER_STATE_KEY_PREFIX}{asset_name}"
        if state_key in state.get("positions", {}):
            continue
        if _on_cooldown(state, cfg["symbol"]):
            continue
        try:
            # N.3: SMA200 slope gate needs 205+ bars; 260 gives headroom
            # (WEEX kline cap is 1000). Without the flag, 120 sufficed.
            n_bars = 260 if cfg.get("use_sma200_filter", False) else 120
            raw = executor.get_klines(cfg["symbol"], cfg["interval"], n_bars)
            df = _build_dataframe(raw)
            slow_n = int(cfg.get("sma_slow", 100))
            if df is None or len(df) < slow_n + 2:
                continue

            sig = analyze_crossover_entry(df, cfg)

            # P2.3 — daily 9-MA regime gate (live parity with replay).
            # Fetch failure → gate degrades to pass.
            # P5 finding 5: classify on COMPLETED daily bars only — the
            # fetch's last row is today's forming candle and would make
            # the label repaint intraday.
            if sig["would_enter"] and cfg.get("use_daily_regime", False):
                try:
                    from regime import (classify_daily_trend,
                                          daily_regime_allows,
                                          completed_daily_closes)
                    raw_1d = executor.get_klines(cfg["symbol"], "1d", 20)
                    df_1d = _build_dataframe(raw_1d)
                    if df_1d is not None and len(df_1d) >= 13:
                        closes_1d = completed_daily_closes(
                            df_1d["close"], last_bar_forming=True)
                        regime_label = classify_daily_trend(closes_1d)
                        if not daily_regime_allows(sig["direction"], regime_label):
                            sig = {**sig, "would_enter": False,
                                    "blocked_by": f"daily_regime_{regime_label}"}
                except Exception as e:  # noqa: BLE001
                    logger.warning("[%s] daily regime fetch failed (pass): %s",
                                    asset_name, e)

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
                # Tier 0.2 — spread air-pocket gate. Skip entry if current
                # spread is an outlier vs the symbol's rolling history.
                air_pocket_mult = float(cfg.get("spread_air_pocket_mult", 3.0))
                if (spread_tracker is not None
                        and spread_tracker.is_air_pocket(
                            cfg["symbol"], multiplier=air_pocket_mult)):
                    cur = spread_tracker.current_bps(cfg["symbol"])
                    mean = spread_tracker.rolling_mean_bps(cfg["symbol"])
                    logger.warning(
                        "[%s] spread air-pocket — skipping entry: "
                        "current=%.2f bps, rolling_mean=%.2f bps, mult=%.1fx",
                        asset_name, cur or 0.0, mean or 0.0, air_pocket_mult)
                    state["signal_status"][asset_name]["blocked_by"] = "spread_air_pocket"
                    continue
                open_crossover_position(
                    executor, state, asset_name, cfg, df, sig["direction"])
                if _count_open_crossovers(state) >= MAX_CROSSOVER_POSITIONS:
                    break
        except Exception as e:  # noqa: BLE001
            logger.error("[%s] entry cycle errored: %s",
                          asset_name, e, exc_info=True)

    save_state(state, owner="crossover")


def run() -> None:
    """Daemon entrypoint — loops forever with poll interval."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Crossover bot starting. PAUSED=%s", CROSSOVER_PAUSED)

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
        sleep_for = max(1, CROSSOVER_POLL_INTERVAL_SECONDS - elapsed)
        logger.info("Cycle %d done in %.1fs. Sleeping %.0fs.", cycle, elapsed, sleep_for)
        time.sleep(sleep_for)


if __name__ == "__main__":
    run()
