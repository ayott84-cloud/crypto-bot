"""Exclusions apply to PERFORMANCE, never to RISK (Aug 22 2026).

The 15 StockRev churn rows are set aside because they measure a poll
loop rather than a strategy. But they were real fills at real prices:
the account actually moved by that amount.

So the boundary matters more than the mechanism:

  PERFORMANCE  win rate, profit factor, per-bot stats, projections
               -> exclude. These answer "does this strategy work",
                  and a defect's output is not evidence about that.

  RISK         24h drawdown, the kill-switch breaker, exposure
               -> NEVER exclude. These answer "how much has the account
                  actually lost", and a filter there would understate
                  real losses and could keep a breaker from tripping.

Getting this backwards is the dangerous direction: a risk breaker that
cannot see real money moving is worse than no exclusion mechanism at all.

Run: python -m pytest tests/test_exclusion_scope.py -v
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pytest

pytest.importorskip("pandas")

import dashboard
import kill_switch


# ─── Risk must see everything ────────────────────────────────────────────

def test_the_kill_switch_never_filters_excluded_trades():
    """A breaker blind to real money moving is worse than no exclusion
    mechanism at all."""
    src = inspect.getsource(kill_switch)
    assert "partition_excluded" not in src, (
        "kill_switch is filtering excluded trades — real losses would "
        "stop counting toward the drawdown breaker")


def test_the_dashboard_kill_switch_panel_does_not_filter():
    src = inspect.getsource(dashboard._v2_kill_switch_panel)
    assert "partition_excluded" not in src, (
        "the 24h drawdown panel would understate actual losses")


def test_a_defect_trade_still_counts_toward_drawdown():
    """End to end: PnL is PnL for risk purposes."""
    from journal import EXCLUDED_PREFIX
    from datetime import datetime
    now = datetime.now().isoformat()
    closed = [{"result": "LOSS", "net_pnl": -50.0, "date_closed": now,
               "notes": f"{EXCLUDED_PREFIX} churn loop]"}]
    assert kill_switch._trailing_pnl(closed, hours=24) == pytest.approx(-50.0)


# ─── Performance must not ────────────────────────────────────────────────

def test_the_dashboard_performance_loader_filters():
    src = inspect.getsource(dashboard._read_journal_trades)
    assert "partition_excluded" in src, (
        "the dashboard would show PF 4.38 for a churn loop while "
        "fleet_review shows n=0 — two views, two truths")


def test_the_performance_loader_announces_what_it_dropped():
    """Same rule as the CLI tools: a silent exclusion is
    indistinguishable from a dishonest one."""
    src = inspect.getsource(dashboard._read_journal_trades)
    assert "excluded_reason" in src or "logger" in src
