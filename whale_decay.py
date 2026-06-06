"""Phase W.C — whale cohort signal-decay tracker.

For every signal generated, record (coin, direction, entry_price, ts).
After holding_period_s elapses, resolve it against the then-current
price: did the cohort's directional call work?

cohort_accuracy_30d returns a rolling 30-day signal-accuracy percentage.
should_alarm fires when accuracy drops below the configured threshold
(default 50%). The whale_main loop checks this each cycle and triggers
a soft auto-pause when fired.

State is persisted to disk so restarts don't lose history.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("crypto_bot.whale_decay")


DEFAULT_HOLDING_PERIOD_S = 24 * 60 * 60      # 24 hours
DEFAULT_ROLLING_WINDOW_S = 30 * 24 * 60 * 60 # 30 days
DEFAULT_ACCURACY_THRESHOLD = 50.0            # below 50%, alarm


def record_signal(decay_state: dict, coin: str, direction: str,
                   entry_price: float, cycle_ts: int) -> None:
    """Append a pending signal to the decay tracker."""
    decay_state.setdefault("pending", []).append({
        "coin":        coin,
        "direction":   direction,
        "entry_price": float(entry_price),
        "ts":          int(cycle_ts),
    })


def score_signal_outcome(coin: str, direction: str,
                          entry_price: float, exit_price: float) -> bool:
    """Did the cohort's directional call work?

    LONG  correct when exit_price > entry_price
    SHORT correct when exit_price < entry_price
    Exactly flat = incorrect (no validation either way).
    """
    if exit_price == entry_price:
        return False
    if direction == "LONG":
        return exit_price > entry_price
    if direction == "SHORT":
        return exit_price < entry_price
    return False


def finalize_signals(decay_state: dict, current_prices: dict,
                      current_ts: int,
                      holding_period_s: int = DEFAULT_HOLDING_PERIOD_S) -> None:
    """Move pending signals whose holding period elapsed into `resolved`,
    scoring each against the supplied current_prices map."""
    pending = decay_state.get("pending", [])
    resolved = decay_state.setdefault("resolved", [])
    still_pending = []
    for sig in pending:
        elapsed = current_ts - int(sig["ts"])
        if elapsed < holding_period_s:
            still_pending.append(sig)
            continue
        exit_price = current_prices.get(sig["coin"])
        if exit_price is None:
            # No price right now; keep pending one more cycle
            still_pending.append(sig)
            continue
        outcome = score_signal_outcome(
            sig["coin"], sig["direction"],
            entry_price=float(sig["entry_price"]),
            exit_price=float(exit_price),
        )
        resolved.append({
            "coin":     sig["coin"],
            "direction": sig["direction"],
            "ts":       int(current_ts),
            "outcome":  bool(outcome),
        })
    decay_state["pending"] = still_pending


def cohort_accuracy_30d(decay_state: dict, now_ts: int,
                         window_s: int = DEFAULT_ROLLING_WINDOW_S) -> float:
    """Returns rolling-window accuracy percentage in [0, 100]."""
    resolved = decay_state.get("resolved", [])
    if not resolved:
        return 0.0
    cutoff = now_ts - window_s
    recent = [r for r in resolved if int(r.get("ts", 0)) >= cutoff]
    if not recent:
        return 0.0
    wins = sum(1 for r in recent if r.get("outcome"))
    return round(wins / len(recent) * 100.0, 2)


def should_alarm(accuracy_pct: float,
                  threshold_pct: float = DEFAULT_ACCURACY_THRESHOLD) -> bool:
    return accuracy_pct < threshold_pct


# ─── Persistence ──────────────────────────────────────────────────────────

def save_decay_state(state: dict, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to save decay state: %s", e)


def load_decay_state(path: Path) -> dict:
    if not path.exists():
        return {"pending": [], "resolved": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("pending", [])
        data.setdefault("resolved", [])
        return data
    except Exception as e:
        logger.warning("Failed to load decay state: %s — starting fresh", e)
        return {"pending": [], "resolved": []}
