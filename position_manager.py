"""Position state management and slot tracking.

Manages the 8-slot position limit, tracks entry metadata,
persists state to state.json, and implements rotation logic.

Concurrency model (two-bot coexistence):
    Both the momentum bot (main.py) and the whale bot (whale_main.py) share
    the same state.json. To avoid clobbering each other's concurrent writes,
    save_state acquires a cross-process lock file (state.json.lock) and
    merges per-namespace:
      - Positions with key prefix "WHALE_" belong to the whale bot.
      - All other position keys belong to the momentum bot.
      - Top-level keys (last_processed_candle, signal_status, ...) belong
        to the momentum bot; whale_cooldowns belongs to the whale bot.
    Each save preserves the other bot's namespace from disk.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional, Tuple

from config import STATE_FILE, MARGIN_PER_TRADE, MAX_POSITIONS, DEFAULT_LEVERAGE

logger = logging.getLogger("crypto_bot.position_mgr")

# ─── Default empty state ────────────────────────────────────────────────────

DEFAULT_STATE = {
    "positions": {},
    "last_processed_candle": {},
    "last_dashboard_update": None,
}

# ─── Namespace rules ────────────────────────────────────────────────────────
#
# Position-key prefixes identify which bot owns the position. New bots add
# a prefix + a row in BOT_PREFIXES below — the rest of the file dispatches
# through _bot_of_key() so the namespace logic stays in one place.

_WHALE_PREFIX = "WHALE_"
_FUNDING_PREFIX = "FUNDING_"
_BREAKOUT_PREFIX = "BREAKOUT_"

# Ordered (prefix, bot) — first match wins. Add new bots here.
BOT_PREFIXES = (
    (_WHALE_PREFIX, "whale"),
    (_FUNDING_PREFIX, "funding"),
    (_BREAKOUT_PREFIX, "breakout"),
)
DEFAULT_BOT = "momentum"

_MOMENTUM_TOPLEVEL = {
    "last_processed_candle", "signal_status", "last_dashboard_update",
}
_WHALE_TOPLEVEL = {"whale_cooldowns"}
_FUNDING_TOPLEVEL: set = set()  # funding bot has no top-level state today
_BREAKOUT_TOPLEVEL: set = set()  # breakout bot has no top-level state today

# Per-bot top-level key sets — used by _merge_state to preserve other bots'
# top-level state when one bot saves.
_TOPLEVEL_BY_BOT = {
    "momentum": _MOMENTUM_TOPLEVEL,
    "whale": _WHALE_TOPLEVEL,
    "funding": _FUNDING_TOPLEVEL,
    "breakout": _BREAKOUT_TOPLEVEL,
}


def _bot_of_key(position_key: str) -> str:
    """Classify a position key by owning bot. Default = momentum."""
    for prefix, bot in BOT_PREFIXES:
        if position_key.startswith(prefix):
            return bot
    return DEFAULT_BOT


def _is_whale_key(position_key: str) -> bool:
    """Backward-compat shim. Prefer _bot_of_key() in new code."""
    return _bot_of_key(position_key) == "whale"


# ─── Cross-process file lock (stdlib only) ──────────────────────────────────

_LOCK_TIMEOUT_S = 10.0
_LOCK_STALE_S = 60.0


@contextmanager
def _state_file_lock() -> Iterator[None]:
    """Cross-process lock for state.json. Uses O_CREAT|O_EXCL on a .lock file.

    On crash the lock file is left behind; we detect staleness by mtime age
    (>60s) and break it. Not perfect — but good enough for two cooperating
    Python processes on one machine.
    """
    lock_path = str(STATE_FILE) + ".lock"
    start = time.time()
    fd = None
    while fd is None:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, f"{os.getpid()} {time.time():.2f}".encode("ascii"))
            finally:
                os.close(fd)
            break
        except FileExistsError:
            # Stale-lock recovery: if the lock file is older than the staleness
            # threshold, break it and retry.
            try:
                age = time.time() - os.path.getmtime(lock_path)
                if age > _LOCK_STALE_S:
                    logger.warning("Breaking stale state lock (age %.1fs)", age)
                    try:
                        os.unlink(lock_path)
                    except OSError:
                        pass
                    continue
            except OSError:
                pass
            if time.time() - start > _LOCK_TIMEOUT_S:
                raise TimeoutError(
                    f"Could not acquire state lock {lock_path} after "
                    f"{_LOCK_TIMEOUT_S}s — another process may be stuck."
                )
            time.sleep(0.05)
        fd = None if fd == -1 else fd  # defensive; normally fd is a valid int
    try:
        yield
    finally:
        try:
            os.unlink(lock_path)
        except OSError:
            pass


# ─── Raw I/O (no lock — callers are responsible) ────────────────────────────

def _load_state_no_lock() -> dict:
    if not STATE_FILE.exists():
        return json.loads(json.dumps(DEFAULT_STATE))
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        for key in DEFAULT_STATE:
            if key not in data:
                data[key] = DEFAULT_STATE[key]
        return data
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("Corrupt state file, starting fresh: %s", e)
        return json.loads(json.dumps(DEFAULT_STATE))


def _atomic_write_no_lock(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    tmp.replace(STATE_FILE)


# ─── Namespace-aware merge ──────────────────────────────────────────────────

def _merge_state(ours: dict, disk: dict, owner: str) -> dict:
    """Merge our in-memory state with the on-disk state by namespace.

    Rules:
        - Positions owned by `owner` come from `ours` (our save wins).
        - Positions owned by ANY OTHER bot are preserved from `disk` —
          we don't touch them, the other bot may have just written them.
        - Top-level keys owned by `owner` come from `ours`; other bots'
          top-level keys are preserved from `disk`.
    """
    result = dict(ours)  # start from our state

    # Positions: keep our owner's keys from ours, preserve all other owners' keys from disk
    our_positions = ours.get("positions", {})
    disk_positions = disk.get("positions", {})
    merged_positions = {k: v for k, v in our_positions.items() if _bot_of_key(k) == owner}
    for k, v in disk_positions.items():
        if k in merged_positions:
            continue
        if _bot_of_key(k) != owner:
            merged_positions[k] = v
    result["positions"] = merged_positions

    # Top-level keys: preserve other bots' top-level keys from disk
    for bot, keys in _TOPLEVEL_BY_BOT.items():
        if bot == owner:
            continue
        for k in keys:
            if k in disk:
                result[k] = disk[k]

    return result


# ─── Public API ─────────────────────────────────────────────────────────────

def load_state() -> dict:
    """Load state from disk under a brief lock to avoid reading mid-write."""
    if not STATE_FILE.exists():
        logger.info("No state file found, starting fresh")
        return json.loads(json.dumps(DEFAULT_STATE))
    try:
        with _state_file_lock():
            return _load_state_no_lock()
    except TimeoutError as e:
        # Lock stuck — fall back to unlocked read. Better to get stale data
        # than to crash. Subsequent save will still merge-safely.
        logger.warning("load_state lock timeout, reading without lock: %s", e)
        return _load_state_no_lock()


def save_state(state: dict, owner: str = "momentum") -> None:
    """Merge-safe save: under a lock, re-read disk and only overwrite our namespace.

    owner: "momentum" (bot 1 / main.py) or "whale" (bot 2 / whale_main.py).
           Default "momentum" keeps backward compatibility for existing callers.
    """
    try:
        with _state_file_lock():
            disk = _load_state_no_lock()
            merged = _merge_state(state, disk, owner)
            _atomic_write_no_lock(merged)
    except TimeoutError as e:
        logger.error("save_state lock timeout — last-resort non-merged write: %s", e)
        try:
            _atomic_write_no_lock(state)
        except IOError as ee:
            logger.error("Failed to save state: %s", ee)
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


def find_most_profitable_position(state: dict, executor, owner: str = "momentum") -> Optional[str]:
    """Find the position with the highest unrealized PnL within the caller's namespace.

    owner: "momentum" only considers non-WHALE_* keys; "whale" only considers
           WHALE_* keys. Default "momentum" preserves backward compatibility,
           but is now SAFE — without this filter, the momentum bot's rotation
           logic was selecting whale SHORT positions via LONG-only PnL math
           and closing them with hardcoded LONG-direction calls (bug observed
           on Trade #10, May 2026).

    PnL math is now direction-aware: LONG profits when price rises, SHORT
    profits when price falls.

    Returns the state_key (dict key in positions, e.g. "XRP" or "XRP_4H"),
    or None if no eligible positions.
    """
    positions = get_open_positions(state)
    if not positions:
        return None

    best_state_key = None
    best_pnl = float("-inf")

    for state_key, pos in positions.items():
        # Filter by owner — never let one bot rotate another bot's positions.
        # Three-way dispatch (momentum/whale/funding) lives in _bot_of_key.
        if _bot_of_key(state_key) != owner:
            continue

        # Prefer explicit stored symbol; fall back to state_key for legacy entries
        exch_symbol = pos.get("symbol", state_key)
        current_price = executor.get_symbol_price(exch_symbol)
        if current_price is None:
            continue
        entry = pos["entry_price"]
        qty = float(pos["quantity"])
        # Direction-aware PnL: LONG profits when price > entry; SHORT inverts.
        # Legacy momentum positions (no direction key) default to LONG.
        direction = pos.get("direction", "LONG")
        sign = 1 if direction == "LONG" else -1
        pnl = (current_price - entry) * qty * sign
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


def reconcile_with_exchange(state: dict, executor, owner: str = "momentum") -> None:
    """Sync state.json with actual exchange positions on startup.

    Matches by (symbol, direction) to support hedge mode (where the momentum bot
    can hold BTCUSDT LONG while the whale bot holds BTCUSDT SHORT on the same
    account). Only reconciles state entries owned by `owner`; the other bot's
    positions are left alone.

    owner: "momentum" reconciles non-WHALE_* keys; "whale" reconciles WHALE_* keys.
    """
    exchange_positions = executor.get_all_positions()

    # Build set of (symbol, "LONG"|"SHORT") currently held on exchange
    exchange_set: set = set()
    for pos in exchange_positions:
        sym = pos.get("symbol", "")
        amt = float(pos.get("positionAmt", "0"))
        if amt > 0:
            exchange_set.add((sym, "LONG"))
        elif amt < 0:
            exchange_set.add((sym, "SHORT"))

    state_positions = state.get("positions", {})

    # Determine which state keys this owner is responsible for.
    # Three-way dispatch via _bot_of_key — extensible to pair/breakout/etc.
    def _owned(key: str) -> bool:
        return _bot_of_key(key) == owner

    # Log unknown positions on exchange (only for our owner's set of expected symbols)
    state_sig_set = set()
    for k, p in state_positions.items():
        if not _owned(k):
            continue
        sym = p.get("symbol", k)
        direction = p.get("direction", "LONG")  # legacy momentum entries default to LONG
        state_sig_set.add((sym, direction))
    for sym, direction in exchange_set - state_sig_set:
        # Untracked exchange position. Might belong to the OTHER bot — only warn
        # if neither bot tracks it. (Simple heuristic: we can't fully verify here.)
        logger.info("Exchange position %s %s not tracked in %s namespace "
                    "(could belong to the other bot or be a manual trade).",
                    sym, direction, owner)

    # Clean up state entries whose (symbol, direction) is not on exchange
    for state_key in list(state_positions.keys()):
        if not _owned(state_key):
            continue
        pos = state_positions[state_key]
        sym = pos.get("symbol", state_key)
        direction = pos.get("direction", "LONG")
        if (sym, direction) not in exchange_set:
            logger.warning("State has %s %s (%s) but exchange does not. "
                           "Cleaning up (closed externally?).",
                           state_key, direction, sym)
            register_exit(state, state_key)

    save_state(state, owner=owner)
