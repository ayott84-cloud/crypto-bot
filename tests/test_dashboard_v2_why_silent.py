"""Phase D.3 — "Why isn't bot X trading?" panel tests.

Run: python -m pytest tests/test_dashboard_v2_why_silent.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pytest.importorskip("jinja2")

import dashboard
from dashboard_renderer import render


# ─── Whale ──────────────────────────────────────────────────────────────────

def test_why_silent_whale_returns_dormant_panel_when_paused():
    """Whale defaults to WHALE_PAUSED=true in the real config."""
    result = dashboard._v2_why_silent("whale", {})
    assert result is not None
    assert result["kind"] == "dormant"
    assert "consensus" in result["detail"].lower()
    assert "12/14" in result["detail"]


# ─── Funding ────────────────────────────────────────────────────────────────

def test_why_silent_funding_awaiting_when_no_closed_trades():
    """0 closed funding trades + not paused → "Awaiting first signal"."""
    data = {"_trades_cache": []}
    result = dashboard._v2_why_silent("funding", data)
    assert result is not None
    assert result["kind"] == "info"
    assert "awaiting" in result["label"].lower()


def test_why_silent_funding_returns_none_when_trades_exist():
    """If the funding bot has ≥1 closed trade, the panel hides."""
    data = {"_trades_cache": [
        {"bot": "Funding", "exit_price": 2500, "net_pnl": 10.0},
    ]}
    assert dashboard._v2_why_silent("funding", data) is None


# ─── Momentum ───────────────────────────────────────────────────────────────

def test_why_silent_momentum_returns_none_when_no_signal_status():
    """If state.signal_status is empty, no diagnosis available — hide."""
    assert dashboard._v2_why_silent("momentum", {"signal_status": {}}) is None
    assert dashboard._v2_why_silent("momentum", {}) is None


def test_why_silent_momentum_returns_none_when_all_strategies_would_enter():
    """If every strategy is signaling, no silence to explain."""
    data = {"signal_status": {
        "BTC":  {"would_enter": True,  "blocked_by": None},
        "ETH":  {"would_enter": True,  "blocked_by": None},
    }}
    assert dashboard._v2_why_silent("momentum", data) is None


def test_why_silent_momentum_picks_most_common_blocker():
    """When multiple filters block, the most-common reason wins."""
    data = {"signal_status": {
        "BTC":  {"would_enter": False, "blocked_by": "btc_filter"},
        "ETH":  {"would_enter": False, "blocked_by": "btc_filter"},
        "XRP":  {"would_enter": False, "blocked_by": "btc_filter"},
        "DOGE": {"would_enter": False, "blocked_by": "rsi_crossover"},
        "ADA":  {"would_enter": False, "blocked_by": "btc_filter"},
    }}
    result = dashboard._v2_why_silent("momentum", data)
    assert result is not None
    assert result["kind"] == "silent"
    # blocker_label("btc_filter") → "BTC below EMA — alt correlation gate"
    assert "BTC" in result["label"]
    assert "EMA" in result["label"]
    # 4 of 5 strategies blocked by btc_filter
    assert "4/5" in result["detail"]


def test_why_silent_momentum_humanizes_via_blocker_label():
    """The label comes from blocker_labels.BLOCKER_LABELS, not the raw key."""
    data = {"signal_status": {
        "BTC": {"would_enter": False, "blocked_by": "atr_regime"},
    }}
    result = dashboard._v2_why_silent("momentum", data)
    # blocker_label("atr_regime") → "Low-volatility regime (ATR below SMA)"
    assert "ATR" in result["label"]
    assert result["label"] != "atr_regime"  # not the raw key


def test_why_silent_momentum_ignores_strategies_that_would_enter():
    """A 'would_enter=True' strategy must not contribute to the blocker count."""
    data = {"signal_status": {
        "BTC":  {"would_enter": False, "blocked_by": "btc_filter"},
        "ETH":  {"would_enter": True,  "blocked_by": None},
    }}
    result = dashboard._v2_why_silent("momentum", data)
    # Only BTC is blocked — but denominator is total strategies (both)
    assert "1/2" in result["detail"]


# ─── Template render ────────────────────────────────────────────────────────

def test_overview_renders_why_panel_for_each_bot_with_silence():
    """In the default deployment all three bots have a 'why' reason."""
    html = render("base.html.j2", dashboard._v2_test_context([]))
    # At least one why-panel rendered (whale is always paused)
    assert "why-panel" in html
    assert "WHY NO TRADES" in html.upper()


def test_overview_renders_dormant_class_for_whale_panel():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert "why-panel--dormant" in html


def test_overview_renders_info_class_for_funding_awaiting_signal():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    # 0 trades → funding shows info-kind panel
    assert "why-panel--info" in html


def test_overview_omits_why_panel_when_bot_is_actively_trading():
    """A bot with recent trades + active signal should NOT show a why panel."""
    # Funding actively trading — its why returns None
    trades = [{
        "id": 1, "date_opened": "2026-06-04",
        "symbol": "ETHUSDT", "direction": "LONG", "strategy": "x",
        "bot": "Funding", "entry_price": 2400, "exit_price": 2500,
        "quantity": 0.5, "leverage": 10, "net_pnl": 10.0,
        "result": "WIN", "exit_reason": "TP",
    }]
    ctx = dashboard._v2_test_context(trades)
    html = render("base.html.j2", ctx)
    # Funding panel should be gone (whale still paused, momentum still has no signal_status)
    # We assert by checking that the actual rendered why-panel--info <div> isn't present
    assert '<div class="why-panel why-panel--info"' not in html
