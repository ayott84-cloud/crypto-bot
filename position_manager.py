"""Position state management and slot tracking.

Manages the 8-slot position limit, tracks entry metadata,
persists state to state.json, and implements rotation logic.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from config import STATE_FILE, MARGIN_PER_TRADE, MAX_POSITIONS, DEFAULT_LEVERAGE

logger = logging.getLogger("crypto_bot.position_mgr")

# ─── Default empty state ────────────────────────────────────────────────────

DEFAULT_STATE = {
    "positions": {},
    "last_processed_candle": {},
    "last_dashboard_update": None,
}


def load_state() -> dict:
    """Load state from disk. Returns default if missing/corrupt."""
    if not STATE_FILE.exists():
        logger.info("No state file found, starting fresh")
        return json.loads(json.dumps(DEFAULT_STATE))
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        # Ensure all required keys exist
        for key in DEFAULT_STATE:
            if key not in data:
                data[key] = DEFAULT_STATE[key]
        return data
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("Corrupt state file, starting fresh: %s", e)
        return json.loads(json.dumps(DEFAULT_STATE))


def save_state(state: dict) -> None:
    """Atomically save state to disk (write tmp, then rename)."""
    tmp = STATE_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except IOError as e:
        logger.error("Failed to save state: %s", e)


def get_open_positions(state: dict) -> dict:
    """Return the positions dict."""
    return state.get("positions", {})


def count_open_positions(state: dict) -> int:
    """Count current open positions."""
    return len(state.get("positions", {}))


def can_open_new_position(state: dict) -> bool:
    """Check if there's room for a new position."""
    return count_open_positions(state) < MAX_POSITIONS


def find_most_profitable_position(state: dict, executor) -> Optional[str]:
    """Find the position with the highest unrealized PnL.

    Returns the state_key (dict key in positions, e.g. "XRP" or "XRP_4H"),
    or None if no positions.

    Uses pos["symbol"] (exchange symbol like "XRPUSDT") for executor.get_symbol_price
    so multiple configs on same underlying symbol can coexist.
    """
    positions = get_open_positions(state)
    if not positions:
        return None

    best_state_key = None
    best_pnl = float("-inf")

    for state_key, pos in positions.items():
        # Prefer explicit stored symbol; fall back to state_key for legacy entries
        exch_symbol = pos.get("symbol", state_key)
        current_price = executor.get_symbol_price(exch_symbol)
        if current_price is None:
            continue
        entry = pos["entry_price"]
        qty = float(pos["quantity"])
        pnl = (current_price - entry) * qty
        if pnl > best_pnl:
            best_pnl = pnl
            best_state_key = state_key

    logger.info("Most profitable position: %s (uPnL: $%.2f)", best_state_key, best_pnl)
    return best_state_key


def register_entry(
    state: dict,
    state_key: str,
    entry_price: float,
    atr_at_entry: float,
    quantity: str,
    strategy: str,
    entry_reason: str = "",
    symbol: Optional[str] = None,
) -> None:
    """Register a new position in state.

    state_key: unique per-strategy dict key (e.g. "XRP", "XRP_4H")
    symbol: exchange symbol (e.g. "XRPUSDT") — stored inside pos for executor calls
    """
    state["positions"][state_key] = {
        "entry_price": entry_price,
        "atr_at_entry": atr_at_entry,
        "quantity": quantity,
        "bars_since_entry": 0,
        "phase": "full",
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "strategy": strategy,
        "entry_reason": entry_reason,
        "symbol": symbol or state_key,  # exchange symbol for API calls
    }
    logger.info("Registered ENTRY %s (%s) @ %.4f, qty=%s, ATR=%.4f",
                state_key, symbol or state_key, entry_price, quantity, atr_at_entry)


def register_tp1_taken(state: dict, symbol: str, new_quantity: str) -> None:
    """Update position after TP1 partial close."""
    pos = state["positions"].get(symbol)
    if pos:
        pos["phase"] = "tp1_taken"
        pos["quantity"] = new_quantity
        logger.info("TP1 taken for %s, remaining qty=%s", symbol, new_quantity)


def register_exit(state: dict, symbol: str) -> dict:
    """Remove position from state and return the position data."""
    pos = state["positions"].pop(symbol, None)
    if pos:
        logger.info("Registered EXIT %s (was %s phase)", symbol, pos.get("phase"))
    return pos or {}


def increment_bar_count(state: dict, symbol: str) -> int:
    """Increment bar count for a position. Returns new count."""
    pos = state["positions"].get(symbol)
    if pos:
        pos["bars_since_entry"] = pos.get("bars_since_entry", 0) + 1
        return pos["bars_since_entry"]
    return 0


def calculate_position_quantity(
    symbol: str,
    current_price: float,
    leverage: int,
    executor,
) -> str:
    """Calculate position quantity based on margin allocation.

    $50 margin * 10x leverage = $500 notional
    quantity = notional / price, rounded to qty step
    """
    notional = MARGIN_PER_TRADE * leverage
    raw_qty = notional / current_price

    # Round down to the symbol's step size
    step = executor.get_qty_step(symbol)
    if step > 0:
        qty = math.floor(raw_qty / step) * step
    else:
        qty = raw_qty

    # Ensure minimum quantity
    min_qty = executor.get_min_qty(symbol)
    if qty < min_qty:
        qty = min_qty

    # Format to avoid floating-point artifacts
    decimals = max(0, -int(math.log10(step))) if step > 0 and step < 1 else 3
    return f"{qty:.{decimals}f}"


def reconcile_with_exchange(state: dict, executor) -> None:
    """Sync state.json with actual exchange positions on startup.

    State keys are now asset_name (e.g. "XRP", "XRP_4H") which may both
    map to the same exchange symbol (XRPUSDT). We reconcile by symbol
    but cleanup by state_key.
    """
    exchange_positions = executor.get_all_positions()
    exchange_symbols = set()

    for pos in exchange_positions:
        sym = pos.get("symbol", "")
        amt = float(pos.get("positionAmt", "0"))
        if amt != 0:
            exchange_symbols.add(sym)

    # Build a map of state_key → exchange_symbol from state
    state_positions = state.get("positions", {})
    state_key_to_symbol = {
        k: v.get("symbol", k) for k, v in state_positions.items()
    }
    state_symbols = set(state_key_to_symbol.values())

    # Positions on exchange but not in state (manual trades)
    for sym in exchange_symbols - state_symbols:
        logger.warning("Exchange has position for %s not tracked in state "
                       "(manual trade?). Ignoring.", sym)

    # State entries whose exchange symbol isn't on exchange → cleanup
    for state_key, sym in list(state_key_to_symbol.items()):
        if sym not in exchange_symbols:
            logger.warning("State has position for %s (%s) but exchange does not. "
                           "Cleaning up (closed externally?).", state_key, sym)
            register_exit(state, state_key)

    save_state(state)
