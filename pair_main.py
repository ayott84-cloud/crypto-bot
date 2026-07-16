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
    PAIR_CONFIG, PAIR_CONFIGS,
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


def leg_keys(pair_name: str) -> tuple[str, str]:
    """Phase K round 5b: parameterized state-key generator per pair.

    Returns (long_leg_key, short_leg_key) — e.g. for pair_name="BTCLTC":
      ("PAIR_BTCLTC_LONG_LEG", "PAIR_BTCLTC_SHORT_LEG").
    """
    return (
        f"{PAIR_STATE_KEY_PREFIX}{pair_name}_LONG_LEG",
        f"{PAIR_STATE_KEY_PREFIX}{pair_name}_SHORT_LEG",
    )


def strategy_tag_for(pair_name: str) -> str:
    """Suffix form ("ETHBTC Pair") — the shape every other bot uses, and
    the shape journal._bot_tag / kill_switch._bot_of classify by endswith.
    The prefix form ("Pair ETHBTC") written before Jul 16 2026 evaded
    both classifiers, journaling every pair leg as bot="Momentum"."""
    return f"{pair_name} {PAIR_STRATEGY_TAG}"


_INTERVAL_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800,
}


def _bars_held(pos: dict, interval: str, now: Optional[datetime] = None) -> int:
    """Whole interval-bars elapsed since the position's entry_time.

    Replaces the per-POLL-cycle counter that made max_hold_bars=5 fire
    "Time Stop" after 25 minutes (5 × 300s polls) instead of 5 daily
    bars — the open→Time Stop→re-enter thrash of Jul 2-16 2026.
    Positions without a parseable entry_time fall back to the legacy
    counter (incremented), never raise."""
    try:
        entry = datetime.fromisoformat(pos["entry_time"])
        if entry.tzinfo is None:
            entry = entry.replace(tzinfo=timezone.utc)
        now = now or datetime.now(timezone.utc)
        secs = _INTERVAL_SECONDS[interval.lower()]
        return max(0, int((now - entry).total_seconds() // secs))
    except (KeyError, TypeError, ValueError):
        return int(pos.get("bars_held") or 0) + 1


def _pair_is_open(state: dict, pair_name: str = "ETHBTC") -> bool:
    """Is the named pair currently holding legs? Defaults to ETHBTC for
    backwards compat with the original single-pair API."""
    long_k, short_k = leg_keys(pair_name)
    positions = state.get("positions", {})
    return long_k in positions or short_k in positions


def open_pair_position(
    executor: Executor, state: dict, direction: str,
    eth_price: float, btc_price: float, current_ratio: float, z: float,
    pair_name: str = "ETHBTC",
    pair_spec: dict | None = None,
) -> None:
    """Open BOTH legs as one logical trade.

    direction: "LONG_ETH_SHORT_BTC" or "SHORT_ETH_LONG_BTC"
    pair_name: which pair in PAIR_CONFIGS. Default "ETHBTC" preserves
               the single-pair caller contract.
    pair_spec: full spec dict from PAIR_CONFIGS[pair_name]; falls back
               to legacy ETHBTC symbols if None.
    """
    if pair_spec is None:
        pair_spec = PAIR_CONFIGS.get(pair_name, {
            "long_symbol":  PAIR_LONG_SYMBOL,
            "short_symbol": PAIR_SHORT_SYMBOL,
            "interval":     PAIR_INTERVAL,
            "cfg":          PAIR_CONFIG,
        })
    long_sym  = pair_spec["long_symbol"]
    short_sym = pair_spec["short_symbol"]
    long_k, short_k = leg_keys(pair_name)

    is_long_eth = direction == "LONG_ETH_SHORT_BTC"  # rich-leg long convention

    notional = PAIR_MARGIN_PER_LEG * PAIR_LEVERAGE  # $500 per leg
    eth_qty = round(notional / eth_price, 6)
    btc_qty = round(notional / btc_price, 6)
    if eth_qty <= 0 or btc_qty <= 0:
        logger.warning("[%s] Pair-open: computed qty <= 0; aborting", pair_name)
        return

    logger.info("PAIR OPEN [%s] %s | ratio=%.6f z=%.2f | %s qty=%s @ %.2f, %s qty=%s @ %.2f",
                pair_name, direction, current_ratio, z,
                long_sym, eth_qty, eth_price, short_sym, btc_qty, btc_price)

    try:
        if is_long_eth:
            executor.open_long(long_sym,  eth_qty)
            executor.open_short(short_sym, btc_qty)
        else:
            executor.open_short(long_sym, eth_qty)
            executor.open_long(short_sym, btc_qty)
    except Exception as e:
        logger.error("[%s] Pair-open exchange call failed: %s", pair_name, e)
        return

    reason = f"{pair_name} pair entry z={z:.2f} ratio={current_ratio:.6f}"
    strategy_tag = strategy_tag_for(pair_name)
    register_entry(
        state, long_k,
        entry_price=eth_price, atr_at_entry=0.0,
        quantity=eth_qty, strategy=strategy_tag,
        entry_reason=reason, symbol=long_sym,
        direction="LONG" if is_long_eth else "SHORT",
        entry_ratio=current_ratio, entry_z=z, pair_name=pair_name,
    )
    register_entry(
        state, short_k,
        entry_price=btc_price, atr_at_entry=0.0,
        quantity=btc_qty, strategy=strategy_tag,
        entry_reason=reason, symbol=short_sym,
        direction="SHORT" if is_long_eth else "LONG",
        entry_ratio=current_ratio, entry_z=z, pair_name=pair_name,
    )

    # Email open notification — single email for the pair (mentions both legs)
    try:
        from notifier import notify_trade_opened
        notify_trade_opened(
            symbol=f"{pair_name} pair ({direction})",
            entry_price=current_ratio,
            quantity=f"{eth_qty} {long_sym[:3]} / {btc_qty} {short_sym[:3]}",
            leverage=PAIR_LEVERAGE,
            sl_price=current_ratio * 1.05 if is_long_eth else current_ratio * 0.95,
            tp1_price=current_ratio if abs(z) < 0.5 else current_ratio * (1 - 0.5 * z / abs(z)),
            tp2_price=current_ratio,
            atr_at_entry=0.0,
            strategy=strategy_tag,
            entry_reason=f"z-score {z:+.2f}, ratio {current_ratio:.6f}",
            direction="LONG" if is_long_eth else "SHORT",
        )
    except Exception as e:
        logger.warning("[%s] pair-open notification failed: %s", pair_name, e)


def close_pair_position(executor: Executor, state: dict, reason: str,
                          pair_name: str = "ETHBTC") -> None:
    """Close BOTH legs of the named pair. Reason applied to both journal
    rows. Defaults to ETHBTC for backwards compat."""
    long_k, short_k = leg_keys(pair_name)
    positions = state.get("positions", {})
    long_leg  = positions.get(long_k)
    short_leg = positions.get(short_k)
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

    _close_one(long_k,  long_leg)
    _close_one(short_k, short_leg)
    logger.info("PAIR [%s] CLOSED — %s", pair_name, reason)

    # Email close notification — single email for the pair
    try:
        from notifier import notify_trade_closed
        # Combine both legs' PnL into one report (use long_leg as anchor)
        anchor = long_leg or short_leg or {}
        entry = float(anchor.get("entry_ratio") or anchor.get("entry_price") or 0)
        # Best-effort current price for ratio display
        pair_spec = PAIR_CONFIGS.get(pair_name, {
            "long_symbol":  PAIR_LONG_SYMBOL,
            "short_symbol": PAIR_SHORT_SYMBOL,
        })
        try:
            eth_now = executor.get_symbol_price(pair_spec["long_symbol"]) or 0
            btc_now = executor.get_symbol_price(pair_spec["short_symbol"]) or 1
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
            symbol=f"{pair_name} pair",
            direction=anchor.get("direction", "LONG"),
            entry_price=entry,
            exit_price=current_ratio,
            quantity=float(anchor.get("quantity") or 0),
            leverage=PAIR_LEVERAGE,
            sl_price=entry * 1.05,
            tp1_price=entry,
            tp2_price=entry,
            exit_reason=reason,
            strategy=strategy_tag_for(pair_name),
            portfolio_value=portfolio_value,
        )
    except Exception as e:
        logger.warning("pair-close notification failed: %s", e)


def _run_one_pair(executor: Executor, state: dict, pair_name: str,
                    pair_spec: dict) -> None:
    """Phase K round 5b — exit-or-entry cycle for a single configured pair.

    Caller iterates PAIR_CONFIGS and calls this once per pair. Each pair
    has its own state keys (PAIR_<NAME>_LONG_LEG / SHORT_LEG) so two
    pairs can be open simultaneously without colliding.
    """
    long_sym  = pair_spec["long_symbol"]
    short_sym = pair_spec["short_symbol"]
    interval  = pair_spec.get("interval", PAIR_INTERVAL)
    cfg       = pair_spec["cfg"]
    long_k, short_k = leg_keys(pair_name)

    try:
        eth_klines = executor.get_klines(
            long_sym,  interval, max(60, cfg["z_window"] * 2))
        btc_klines = executor.get_klines(
            short_sym, interval, max(60, cfg["z_window"] * 2))
    except Exception as e:
        logger.error("[%s] Failed to fetch klines: %s", pair_name, e)
        return

    eth_close = _closes_from_klines(eth_klines)
    btc_close = _closes_from_klines(btc_klines)

    # 1. Exit-management for an open pair
    if _pair_is_open(state, pair_name):
        long_leg = state.get("positions", {}).get(long_k) or {}
        bars_held = _bars_held(long_leg, interval)
        entry_ratio = float(long_leg.get("entry_ratio") or 0.0)
        pos_dir = ("LONG_ETH_SHORT_BTC"
                    if long_leg.get("direction") == "LONG"
                    else "SHORT_ETH_LONG_BTC")
        reason, _kind = check_pair_exit(
            eth_close, btc_close,
            position_direction=pos_dir,
            bars_held=bars_held, entry_ratio=entry_ratio,
            cfg=cfg,
        )
        if reason:
            close_pair_position(executor, state, reason, pair_name=pair_name)
        else:
            for k in (long_k, short_k):
                if k in state.get("positions", {}):
                    state["positions"][k]["bars_held"] = bars_held
        return  # don't open a new leg same cycle this pair just acted

    # 2. Pause + entry. Pause is a global flag — short-circuit both pairs.
    if PAIR_PAUSED:
        return

    sig = analyze_pair_entry(eth_close, btc_close, cfg)
    if sig["would_enter"]:
        eth_price = float(eth_close.iloc[-1])
        btc_price = float(btc_close.iloc[-1])
        open_pair_position(
            executor, state, sig["direction"],
            eth_price=eth_price, btc_price=btc_price,
            current_ratio=sig["ratio"] or 0.0, z=sig["z"] or 0.0,
            pair_name=pair_name, pair_spec=pair_spec,
        )


def run_cycle(executor: Executor, state: dict) -> None:
    """One full poll — iterates every configured pair in PAIR_CONFIGS."""
    _write_heartbeat(_HEARTBEAT_FILE)

    for pair_name, pair_spec in PAIR_CONFIGS.items():
        try:
            _run_one_pair(executor, state, pair_name, pair_spec)
        except Exception as e:  # noqa: BLE001
            logger.error("[%s] pair cycle errored: %s", pair_name, e,
                          exc_info=True)

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
