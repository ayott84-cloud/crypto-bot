"""Dashboard build-failure watchdog tests.

Background: a Jun 21 2026 consolidation commit shipped templates that
referenced new context keys, and bots running OLD Python bytecode (from
before the pull) silently fired `Dashboard regen failed: 'scalp_meta'
is undefined` every cycle for 24 hours without anyone noticing — because
the failure was logged at WARN, not ERROR.

This watchdog adds escalation:
  - N=1 to N=2 failures: WARN (per-cycle, brief, current behaviour)
  - N=3+ failures:       ERROR with full traceback — impossible to miss
                          in `journalctl -p err`
  - N=5+ failures:       Discord notification (one-shot — fires once at
                          the threshold crossing, not every subsequent cycle)
  - Recovery:            INFO log on the success cycle that ended a
                          ≥3-streak, plus streak counter resets

Plus a selfcheck_dashboard_render() helper bots call at startup so
template/context drift fails LOUDLY immediately instead of waiting
3+ poll cycles (which can be 45 min on whale).

Run: python -m pytest tests/test_dashboard_watchdog.py -v
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest


@pytest.fixture(autouse=True)
def _reset_failure_streak():
    """Each test gets a clean module-level state."""
    import dashboard
    dashboard._DASHBOARD_FAILURE_STREAK.clear()
    dashboard._DASHBOARD_NOTIFIED_AT_STREAK.clear()
    yield
    dashboard._DASHBOARD_FAILURE_STREAK.clear()
    dashboard._DASHBOARD_NOTIFIED_AT_STREAK.clear()


# ─── build_dashboard_safely — wrapper behaviour ────────────────────────────

def test_safely_returns_true_on_success():
    from dashboard import build_dashboard_safely
    with patch("dashboard.build_dashboard"):
        ok = build_dashboard_safely(MagicMock(), {}, bot_owner="momentum")
    assert ok is True


def test_safely_returns_false_on_failure():
    from dashboard import build_dashboard_safely
    with patch("dashboard.build_dashboard", side_effect=KeyError("scalp_meta")):
        ok = build_dashboard_safely(MagicMock(), {}, bot_owner="momentum")
    assert ok is False


def test_safely_never_raises_even_when_inner_raises():
    """Bot's run_cycle must not crash on a dashboard regression."""
    from dashboard import build_dashboard_safely
    with patch("dashboard.build_dashboard",
                side_effect=RuntimeError("template not found")):
        build_dashboard_safely(MagicMock(), {}, bot_owner="whale")
    # If we reach this line, no exception leaked out — test passes.


# ─── Failure escalation ────────────────────────────────────────────────────

def test_first_failure_logs_warn_not_error(caplog):
    from dashboard import build_dashboard_safely
    with patch("dashboard.build_dashboard", side_effect=KeyError("scalp_meta")):
        with caplog.at_level(logging.WARNING, logger="crypto_bot.dashboard"):
            build_dashboard_safely(MagicMock(), {}, bot_owner="funding")
    warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(warn_records) >= 1
    assert len(error_records) == 0


def test_third_consecutive_failure_escalates_to_error_with_traceback(caplog):
    """N=3 trips the ERROR escalation — should include traceback text so
    the operator sees it in `journalctl -p err`."""
    from dashboard import build_dashboard_safely
    with patch("dashboard.build_dashboard", side_effect=KeyError("scalp_meta")):
        with caplog.at_level(logging.WARNING, logger="crypto_bot.dashboard"):
            for _ in range(3):
                build_dashboard_safely(MagicMock(), {}, bot_owner="breakout")
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) >= 1
    # The ERROR record should reference both the bot owner and the streak count
    err_msg = " ".join(r.getMessage() for r in error_records)
    assert "breakout" in err_msg
    assert "3" in err_msg


def test_streak_isolated_per_bot_owner(caplog):
    """A failure for 'funding' shouldn't trip the escalation threshold for
    'breakout'."""
    from dashboard import build_dashboard_safely
    with patch("dashboard.build_dashboard", side_effect=KeyError("scalp_meta")):
        with caplog.at_level(logging.WARNING, logger="crypto_bot.dashboard"):
            for _ in range(3):
                build_dashboard_safely(MagicMock(), {}, bot_owner="funding")
            # Single failure for breakout should be WARN only, NOT ERROR
            caplog.clear()
            build_dashboard_safely(MagicMock(), {}, bot_owner="breakout")
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) == 0


def test_streak_resets_on_success(caplog):
    """After a success, the streak counter resets to zero — next failure
    starts fresh from N=1, not from where it left off."""
    from dashboard import build_dashboard_safely
    side_effects = [KeyError("scalp_meta")] * 2 + [None] + [KeyError("scalp_meta")] * 2
    with patch("dashboard.build_dashboard", side_effect=side_effects):
        with caplog.at_level(logging.INFO, logger="crypto_bot.dashboard"):
            for _ in range(5):
                build_dashboard_safely(MagicMock(), {}, bot_owner="momentum")
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    # Streak: F=1 WARN, F=2 WARN, S=reset, F=1 WARN, F=2 WARN — never hits N=3 ERROR
    assert len(error_records) == 0


def test_recovery_logs_info_message_when_streak_was_significant(caplog):
    """If we were in an ERROR-level streak and recover, log INFO message
    so the operator sees the transition in normal logs."""
    from dashboard import build_dashboard_safely
    side_effects = [KeyError("scalp_meta")] * 3 + [None]
    with patch("dashboard.build_dashboard", side_effect=side_effects):
        with caplog.at_level(logging.INFO, logger="crypto_bot.dashboard"):
            for _ in range(4):
                build_dashboard_safely(MagicMock(), {}, bot_owner="pair")
    info_records = [r for r in caplog.records
                    if r.levelno == logging.INFO and "RECOVERED" in r.getMessage()]
    assert len(info_records) == 1
    assert "pair" in info_records[0].getMessage()


# ─── Discord notification ──────────────────────────────────────────────────

def test_fifth_failure_attempts_discord_notification():
    """N=5 fires a one-shot Discord notification so the operator gets pinged
    even if they're not actively reading journalctl."""
    from dashboard import build_dashboard_safely
    discord_mock = MagicMock(return_value=True)
    with patch("dashboard.build_dashboard", side_effect=KeyError("scalp_meta")), \
         patch("dashboard._notify_dashboard_failure", discord_mock):
        for _ in range(5):
            build_dashboard_safely(MagicMock(), {}, bot_owner="whale")
    assert discord_mock.call_count == 1
    # The bot owner + streak count should be in the notification args
    call_args = discord_mock.call_args
    assert "whale" in str(call_args)


def test_subsequent_failures_after_notification_do_not_double_notify():
    """Once notified at N=5, don't keep notifying every cycle. Single ping
    per streak; recovery + new failure can re-trigger."""
    from dashboard import build_dashboard_safely
    discord_mock = MagicMock(return_value=True)
    with patch("dashboard.build_dashboard", side_effect=KeyError("scalp_meta")), \
         patch("dashboard._notify_dashboard_failure", discord_mock):
        for _ in range(10):
            build_dashboard_safely(MagicMock(), {}, bot_owner="whale")
    assert discord_mock.call_count == 1  # not 6


def test_discord_notifier_failure_does_not_compound():
    """If the notifier itself raises, the watchdog should not propagate
    the exception — that'd make the bot's run_cycle crash."""
    from dashboard import build_dashboard_safely
    with patch("dashboard.build_dashboard", side_effect=KeyError("scalp_meta")), \
         patch("dashboard._notify_dashboard_failure",
                side_effect=RuntimeError("discord webhook 500")):
        for _ in range(5):
            build_dashboard_safely(MagicMock(), {}, bot_owner="momentum")
    # If we got here without exception, the test passes.


# ─── Startup self-check ────────────────────────────────────────────────────

def test_selfcheck_returns_ok_on_success():
    from dashboard import selfcheck_dashboard_render
    with patch("dashboard.build_dashboard"):
        ok, err = selfcheck_dashboard_render(state={"positions": {}})
    assert ok is True
    assert err is None


def test_selfcheck_returns_error_on_failure():
    from dashboard import selfcheck_dashboard_render
    with patch("dashboard.build_dashboard", side_effect=KeyError("scalp_meta")):
        ok, err = selfcheck_dashboard_render(state={"positions": {}})
    assert ok is False
    assert err is not None
    assert "scalp_meta" in err


def test_selfcheck_never_raises():
    from dashboard import selfcheck_dashboard_render
    with patch("dashboard.build_dashboard",
                side_effect=RuntimeError("catastrophic")):
        ok, err = selfcheck_dashboard_render(state={"positions": {}})
    assert ok is False
