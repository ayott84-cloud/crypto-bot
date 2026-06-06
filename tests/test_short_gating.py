"""Phase E.3 — SHORT enablement gating + direction-aware kill switch.

Three concerns:
  1. is_short_enabled(cfg) — per-asset gate. Defaults to False so SHORT
     never fires on an asset the operator hasn't explicitly enabled.
  2. sl_atr_mult_for(cfg, direction) — picks the right SL multiplier;
     defaults sl_atr_mult_short to 0.8 (tighter than LONG's 1.0).
  3. kill_switch.should_pause(owner, direction=None) — optional direction
     filter so consecutive-LONG-loss and consecutive-SHORT-loss streaks
     are tracked independently (per plan E.3: 2-loss limit on SHORT side).

Run: python -m pytest tests/test_short_gating.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

import signals
import kill_switch


# ─── is_short_enabled ──────────────────────────────────────────────────────

def test_is_short_enabled_defaults_to_false():
    """No explicit allow_short → False. Safe default for every asset."""
    assert signals.is_short_enabled({}) is False
    assert signals.is_short_enabled({"some_other_key": 1}) is False


def test_is_short_enabled_true_when_explicitly_set():
    assert signals.is_short_enabled({"allow_short": True}) is True


def test_is_short_enabled_false_when_explicitly_disabled():
    assert signals.is_short_enabled({"allow_short": False}) is False


# ─── sl_atr_mult_for ───────────────────────────────────────────────────────

def test_sl_atr_mult_for_long_returns_long_multiplier():
    cfg = {"sl_atr_mult": 1.5, "sl_atr_mult_short": 0.9}
    assert signals.sl_atr_mult_for(cfg, "LONG") == 1.5


def test_sl_atr_mult_for_short_returns_short_multiplier():
    cfg = {"sl_atr_mult": 1.5, "sl_atr_mult_short": 0.9}
    assert signals.sl_atr_mult_for(cfg, "SHORT") == 0.9


def test_sl_atr_mult_for_short_defaults_when_not_configured():
    """No sl_atr_mult_short configured → default 0.8 (tighter than LONG)."""
    cfg = {"sl_atr_mult": 1.5}
    assert signals.sl_atr_mult_for(cfg, "SHORT") == 0.8


def test_sl_atr_mult_for_unknown_direction_falls_back_to_long():
    cfg = {"sl_atr_mult": 1.5, "sl_atr_mult_short": 0.9}
    assert signals.sl_atr_mult_for(cfg, "") == 1.5


# ─── Kill switch direction filter ──────────────────────────────────────────

def _trade(strategy, direction, result, hours_ago=1):
    """Build a closed-trade row in the format kill_switch expects."""
    dt = datetime.now() - timedelta(hours=hours_ago)
    return {
        "strategy": strategy,
        "direction": direction,
        "result": result,
        "date_closed": dt.isoformat(),
        "net_pnl": 10.0 if result == "WIN" else -10.0,
    }


@patch("journal.read_trades")
def test_should_pause_short_counts_only_short_losses(mock_read):
    """3 LONG losses + 1 SHORT loss should NOT trigger SHORT 2-loss limit."""
    mock_read.return_value = [
        _trade("BTC 1D Momentum", "LONG",  "LOSS", hours_ago=8),
        _trade("BTC 1D Momentum", "LONG",  "LOSS", hours_ago=6),
        _trade("BTC 1D Momentum", "LONG",  "LOSS", hours_ago=4),
        _trade("BTC 1D Momentum", "SHORT", "LOSS", hours_ago=2),
    ]
    status = kill_switch.should_pause("momentum", direction="SHORT")
    assert status.paused is False


@patch("journal.read_trades")
def test_should_pause_short_triggers_after_2_consecutive_short_losses(mock_read):
    """2 consecutive SHORT losses should pause the SHORT side."""
    mock_read.return_value = [
        _trade("BTC 1D Momentum", "SHORT", "LOSS", hours_ago=4),
        _trade("BTC 1D Momentum", "SHORT", "LOSS", hours_ago=2),
    ]
    status = kill_switch.should_pause("momentum", direction="SHORT")
    assert status.paused is True
    assert "SHORT" in status.reason or "short" in status.reason.lower()


@patch("journal.read_trades")
def test_should_pause_short_resets_after_win(mock_read):
    """A SHORT win between losses resets the SHORT streak."""
    mock_read.return_value = [
        _trade("BTC 1D Momentum", "SHORT", "LOSS", hours_ago=6),
        _trade("BTC 1D Momentum", "SHORT", "WIN",  hours_ago=4),
        _trade("BTC 1D Momentum", "SHORT", "LOSS", hours_ago=2),
    ]
    status = kill_switch.should_pause("momentum", direction="SHORT")
    assert status.paused is False


@patch("journal.read_trades")
def test_should_pause_long_unchanged_when_no_direction_passed(mock_read):
    """Calls without direction= still use the original 5-loss LONG-or-mixed limit."""
    mock_read.return_value = [
        _trade("BTC 1D Momentum", "LONG", "LOSS", hours_ago=h)
        for h in (10, 8, 6, 4, 2)
    ]
    status = kill_switch.should_pause("momentum")
    assert status.paused is True
