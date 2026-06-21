"""Tier 0.1 — extend kill switch to scalp + crossover owners.

Phase M (scalp) and Phase N.2 (crossover) main loops don't currently call
should_pause(owner) before opening positions. Step 1 of this Tier ships:
  - _bot_of() recognizes scalp/crossover strategy tags
  - _filter_to_owner() supports scalp/crossover owners
  - status_summary() includes scalp + crossover keys
  - should_pause("scalp") and should_pause("crossover") work end-to-end

Once these tests pass, the scalp_main.py + crossover_main.py run_cycle paths
will get the same `should_pause(owner)` integration that main.py + whale_main.py
already use.

Run: python -m pytest tests/test_kill_switch_scalp_crossover.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import kill_switch


def _trade(strategy, result, hours_ago=1, direction="LONG"):
    """Build a closed-trade row in the format kill_switch expects."""
    dt = datetime.now() - timedelta(hours=hours_ago)
    return {
        "strategy": strategy,
        "direction": direction,
        "result": result,
        "date_closed": dt.isoformat(),
        "net_pnl": 10.0 if result == "WIN" else -10.0,
    }


# ─── _bot_of classifies scalp / crossover correctly ────────────────────────

def test_bot_of_classifies_scalp_strategy_name():
    """Per-asset names look like 'BTC 5m Scalp' — should classify as scalp."""
    assert kill_switch._bot_of("BTC 5m Scalp") == "scalp"
    assert kill_switch._bot_of("ETH 5m Scalp") == "scalp"
    assert kill_switch._bot_of("LINK 5m Scalp") == "scalp"


def test_bot_of_classifies_crossover_strategy_name():
    """Per-asset names look like 'ETH 1h Crossover' — should classify as crossover."""
    assert kill_switch._bot_of("ETH 1h Crossover") == "crossover"
    assert kill_switch._bot_of("SOL 1h Crossover") == "crossover"
    assert kill_switch._bot_of("XRP 1h Crossover") == "crossover"


def test_bot_of_classifies_scalp_strategy_tag_fallback():
    """Plain 'Scalp' tag (fallback when strategy_name is missing) classifies too."""
    assert kill_switch._bot_of("Scalp") == "scalp"


def test_bot_of_classifies_crossover_strategy_tag_fallback():
    assert kill_switch._bot_of("Crossover") == "crossover"


def test_bot_of_existing_classifiers_unchanged():
    """Regression: don't break momentum/whale/funding classification."""
    assert kill_switch._bot_of("BTC 1D Momentum") == "momentum"
    assert kill_switch._bot_of("Whale Track ETH") == "whale"
    assert kill_switch._bot_of("Funding Fade BTC") == "funding"
    assert kill_switch._bot_of("unknown nonsense") == "momentum"  # default
    assert kill_switch._bot_of(None) == "momentum"
    assert kill_switch._bot_of(123) == "momentum"


# ─── _filter_to_owner supports scalp / crossover ───────────────────────────

def test_filter_to_owner_scalp():
    trades = [
        _trade("BTC 5m Scalp", "LOSS"),
        _trade("ETH 1h Crossover", "LOSS"),
        _trade("BTC 1D Momentum", "LOSS"),
    ]
    scalp_only = kill_switch._filter_to_owner(trades, "scalp")
    assert len(scalp_only) == 1
    assert scalp_only[0]["strategy"] == "BTC 5m Scalp"


def test_filter_to_owner_crossover():
    trades = [
        _trade("BTC 5m Scalp", "LOSS"),
        _trade("ETH 1h Crossover", "LOSS"),
        _trade("SOL 1h Crossover", "WIN"),
        _trade("BTC 1D Momentum", "LOSS"),
    ]
    crossover_only = kill_switch._filter_to_owner(trades, "crossover")
    assert len(crossover_only) == 2
    assert all("Crossover" in t["strategy"] for t in crossover_only)


# ─── should_pause("scalp"|"crossover") end-to-end ──────────────────────────

@patch("journal.read_trades")
def test_should_pause_scalp_triggers_after_5_consecutive_losses(mock_read):
    """5 consecutive scalp losses → pause scalp owner."""
    mock_read.return_value = [
        _trade("BTC 5m Scalp", "LOSS", hours_ago=h)
        for h in (10, 8, 6, 4, 2)
    ]
    status = kill_switch.should_pause("scalp")
    assert status.paused is True
    assert "scalp" in status.reason


@patch("journal.read_trades")
def test_should_pause_scalp_not_triggered_by_other_bots_losses(mock_read):
    """5 momentum losses should NOT pause scalp."""
    mock_read.return_value = [
        _trade("BTC 1D Momentum", "LOSS", hours_ago=h)
        for h in (10, 8, 6, 4, 2)
    ]
    status = kill_switch.should_pause("scalp")
    assert status.paused is False


@patch("journal.read_trades")
def test_should_pause_crossover_triggers_after_5_consecutive_losses(mock_read):
    mock_read.return_value = [
        _trade("ETH 1h Crossover", "LOSS", hours_ago=h)
        for h in (10, 8, 6, 4, 2)
    ]
    status = kill_switch.should_pause("crossover")
    assert status.paused is True
    assert "crossover" in status.reason


@patch("journal.read_trades")
def test_should_pause_crossover_resets_after_win(mock_read):
    """A win between losses resets the crossover streak."""
    mock_read.return_value = [
        _trade("ETH 1h Crossover", "LOSS", hours_ago=10),
        _trade("ETH 1h Crossover", "LOSS", hours_ago=8),
        _trade("ETH 1h Crossover", "WIN",  hours_ago=6),
        _trade("ETH 1h Crossover", "LOSS", hours_ago=4),
        _trade("ETH 1h Crossover", "LOSS", hours_ago=2),
    ]
    status = kill_switch.should_pause("crossover")
    assert status.paused is False


@patch("journal.read_trades")
def test_global_daily_drawdown_still_pauses_scalp(mock_read):
    """Daily-DD breaker is account-wide; scalp gets paused even if its own
    streak is fine, when aggregate 24h PnL crosses the threshold."""
    # 600 USD net loss in last 24h — should trigger MAX_DAILY_DRAWDOWN_USD=-500
    mock_read.return_value = [
        {"strategy": "BTC 1D Momentum", "direction": "LONG",
         "result": "LOSS",
         "date_closed": (datetime.now() - timedelta(hours=2)).isoformat(),
         "net_pnl": -600.0},
    ]
    status = kill_switch.should_pause("scalp")
    assert status.paused is True
    assert "daily drawdown" in status.reason.lower()


# ─── status_summary now reports scalp + crossover ─────────────────────────

@patch("journal.read_trades")
def test_status_summary_includes_scalp_and_crossover(mock_read):
    mock_read.return_value = []
    summary = kill_switch.status_summary()
    assert "scalp" in summary
    assert "crossover" in summary
    # Regression: existing keys still present
    assert "momentum" in summary
    assert "whale" in summary
    assert "funding" in summary


# ─── Latent classification fix: breakout / pair / reversal ────────────────
# Pre-existing bug: their losses were silently classified as "momentum"
# and counted toward momentum's streak instead of their own.

def test_bot_of_classifies_breakout_strategy_name():
    assert kill_switch._bot_of("BTC 4H Breakout") == "breakout"
    assert kill_switch._bot_of("ETH 4H Breakout") == "breakout"
    assert kill_switch._bot_of("SOL 1H Breakout") == "breakout"
    assert kill_switch._bot_of("Breakout") == "breakout"  # bare tag fallback


def test_bot_of_classifies_pair_strategy_name():
    assert kill_switch._bot_of("ETHBTC Pair") == "pair"
    assert kill_switch._bot_of("Pair") == "pair"  # bare tag fallback


def test_bot_of_classifies_reversal_strategy_name():
    assert kill_switch._bot_of("BTC 1D Reversal") == "reversal"
    assert kill_switch._bot_of("Reversal") == "reversal"  # bare tag fallback


def test_filter_to_owner_breakout():
    trades = [
        _trade("BTC 4H Breakout", "LOSS"),
        _trade("ETH 1h Crossover", "LOSS"),
        _trade("BTC 1D Momentum", "LOSS"),
    ]
    breakout_only = kill_switch._filter_to_owner(trades, "breakout")
    assert len(breakout_only) == 1
    assert breakout_only[0]["strategy"] == "BTC 4H Breakout"


@patch("journal.read_trades")
def test_should_pause_breakout_not_triggered_by_momentum_losses(mock_read):
    """5 momentum losses must NOT pause breakout (pre-existing latent bug)."""
    mock_read.return_value = [
        _trade("BTC 1D Momentum", "LOSS", hours_ago=h)
        for h in (10, 8, 6, 4, 2)
    ]
    status = kill_switch.should_pause("breakout")
    assert status.paused is False


@patch("journal.read_trades")
def test_should_pause_breakout_triggers_after_5_consecutive_losses(mock_read):
    mock_read.return_value = [
        _trade("BTC 4H Breakout", "LOSS", hours_ago=h)
        for h in (10, 8, 6, 4, 2)
    ]
    status = kill_switch.should_pause("breakout")
    assert status.paused is True
    assert "breakout" in status.reason


@patch("journal.read_trades")
def test_status_summary_includes_breakout_pair_reversal(mock_read):
    mock_read.return_value = []
    summary = kill_switch.status_summary()
    assert "breakout" in summary
    assert "pair" in summary
    assert "reversal" in summary
