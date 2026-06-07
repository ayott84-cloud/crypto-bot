"""Regression test: build_dashboard() must run without NameError or other
crashes when called from the bot loops.

Caught after a bug landed in production where _build_v2_context referenced
an unbound `state` variable. The bot's try/except swallowed the NameError
and kept running while dashboard.html went stale for ~9 hours. This test
exercises the real call path so the same class of bug can't return.

Run: python -m pytest tests/test_dashboard_build_smoke.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pytest.importorskip("jinja2")


def _mock_executor():
    ex = MagicMock()
    ex.get_klines.return_value = [
        [1_700_000_000_000 + i * 60_000, 100.0, 101.0, 99.0, 100.0,
         1000.0, 1_700_000_000_000 + (i + 1) * 60_000, 100000, 50, 500, 50000]
        for i in range(100)
    ]
    ex.get_account_balance.return_value = {"balance": "5000", "availableBalance": "5000"}
    ex.get_symbol_price.return_value = 100.0
    return ex


def test_build_dashboard_runs_without_crash_with_empty_state(tmp_path, monkeypatch):
    """The exact call shape from the bot loops — empty state."""
    import dashboard

    monkeypatch.setattr(dashboard, "DASHBOARD_FILE", tmp_path / "dashboard.html")
    # Mock the journal so it doesn't hit a real DB
    with patch.object(dashboard, "_read_journal_trades", return_value=[]):
        dashboard.build_dashboard(_mock_executor(), {"positions": {}})

    assert (tmp_path / "dashboard.html").exists()
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html
    assert "</html>" in html


def test_build_dashboard_runs_without_crash_with_open_pair_position(tmp_path, monkeypatch):
    """State with an open PAIR_ETHBTC_LONG_LEG — must not crash."""
    import dashboard

    monkeypatch.setattr(dashboard, "DASHBOARD_FILE", tmp_path / "dashboard.html")
    state = {"positions": {
        "PAIR_ETHBTC_LONG_LEG": {
            "direction":    "LONG",
            "entry_price":  2000.0,
            "entry_ratio":  0.045,
            "entry_z":      -2.31,
            "bars_held":    2,
            "quantity":     0.25,
            "atr_at_entry": 0.0,
            "symbol":       "ETHUSDT",
            "strategy":     "Pair",
        },
    }}
    with patch.object(dashboard, "_read_journal_trades", return_value=[]):
        dashboard.build_dashboard(_mock_executor(), state)

    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    # Pair tab should reflect the open position
    assert "Open position" in html or "open_position" in html or "PAIR" in html


def test_dashboard_inlines_lightweight_charts_library(tmp_path, monkeypatch):
    """J.2: TWLC must be inlined into dashboard.html at build time."""
    import dashboard
    monkeypatch.setattr(dashboard, "DASHBOARD_FILE", tmp_path / "dashboard.html")
    with patch.object(dashboard, "_read_journal_trades", return_value=[]):
        dashboard.build_dashboard(_mock_executor(), {"positions": {}})
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    # License header from the vendored TWLC bundle confirms inlining
    assert "TradingView Lightweight Charts" in html
    # The init helper from dashboard.js must be present too
    assert "window.initAssetChart" in html


def test_build_v2_context_state_argument_optional():
    """The state argument is optional — callers without state still work."""
    import dashboard
    ctx = dashboard._build_v2_context(
        {"_trades_cache": [], "signal_status": {}, "metrics": {}},
    )
    assert "pair_meta" in ctx
    # Without state, the open_position field should be None / absent
    assert ctx["pair_meta"].get("open_position") in (None, {})
