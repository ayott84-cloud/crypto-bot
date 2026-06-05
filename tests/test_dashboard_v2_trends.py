"""Phase D.7d — trend arrows on bot card metrics.

The Overview bot cards should show whether a bot's Net PnL and Win Rate
are improving or degrading. We compare the trailing N-day window against
the prior N-day window of equal length.

Direction semantics:
  - "up"   → current window better than prior (positive trend)
  - "down" → current window worse than prior
  - "flat" → equal, or insufficient data to compare

Run: python -m pytest tests/test_dashboard_v2_trends.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

import dashboard


def _trade(bot, days_ago, net_pnl, result):
    """Build a closed-trade row for a given bot, N days ago, with PnL + result."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "id": 1,
        "date_opened": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "date_closed": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": "BTCUSDT", "direction": "LONG",
        "strategy": "x", "bot": bot,
        "entry_price": 100, "exit_price": 105 if net_pnl > 0 else 95,
        "quantity": 1, "leverage": 10,
        "net_pnl": net_pnl, "result": result,
        "exit_reason": "",
    }


# ─── Net PnL trend ─────────────────────────────────────────────────────────

def test_pnl_trend_up_when_recent_window_outperforms():
    """Last 30 days net PnL +$50 vs prior 30 days +$20 → up."""
    trades = [
        # Current 30d window: +$50 net
        _trade("Momentum",  5, +30.0, "WIN"),
        _trade("Momentum", 10, +20.0, "WIN"),
        # Prior 30d window (30-60d ago): +$20 net
        _trade("Momentum", 40, +30.0, "WIN"),
        _trade("Momentum", 50, -10.0, "LOSS"),
    ]
    t = dashboard._v2_trend(trades, "Momentum", "net_pnl", days=30)
    assert t["direction"] == "up"
    assert t["delta"] == pytest.approx(30.0)


def test_pnl_trend_down_when_recent_window_underperforms():
    trades = [
        _trade("Momentum",  5, -20.0, "LOSS"),
        _trade("Momentum", 40, +30.0, "WIN"),
    ]
    t = dashboard._v2_trend(trades, "Momentum", "net_pnl", days=30)
    assert t["direction"] == "down"


def test_pnl_trend_flat_when_no_trades_in_either_window():
    trades = []
    t = dashboard._v2_trend(trades, "Momentum", "net_pnl", days=30)
    assert t["direction"] == "flat"
    assert t["available"] is False


def test_pnl_trend_up_when_prior_window_empty_but_current_positive():
    """First-signal case: prior window has no trades, current is positive."""
    trades = [_trade("Funding", 5, +25.0, "WIN")]
    t = dashboard._v2_trend(trades, "Funding", "net_pnl", days=30)
    assert t["direction"] == "up"


def test_pnl_trend_down_when_prior_window_empty_but_current_negative():
    trades = [_trade("Funding", 5, -25.0, "LOSS")]
    t = dashboard._v2_trend(trades, "Funding", "net_pnl", days=30)
    assert t["direction"] == "down"


# ─── Win-rate trend ────────────────────────────────────────────────────────

def test_wr_trend_up_when_recent_winrate_higher():
    trades = [
        # Current: 2W / 1L → 67%
        _trade("Momentum",  5, +10.0, "WIN"),
        _trade("Momentum", 10, +10.0, "WIN"),
        _trade("Momentum", 15, -10.0, "LOSS"),
        # Prior: 1W / 2L → 33%
        _trade("Momentum", 40, +10.0, "WIN"),
        _trade("Momentum", 45, -10.0, "LOSS"),
        _trade("Momentum", 50, -10.0, "LOSS"),
    ]
    t = dashboard._v2_trend(trades, "Momentum", "win_rate", days=30)
    assert t["direction"] == "up"


def test_wr_trend_down_when_recent_winrate_lower():
    trades = [
        # Current: 1W / 2L → 33%
        _trade("Momentum",  5, +10.0, "WIN"),
        _trade("Momentum", 10, -10.0, "LOSS"),
        _trade("Momentum", 15, -10.0, "LOSS"),
        # Prior: 2W / 1L → 67%
        _trade("Momentum", 40, +10.0, "WIN"),
        _trade("Momentum", 45, +10.0, "WIN"),
        _trade("Momentum", 50, -10.0, "LOSS"),
    ]
    t = dashboard._v2_trend(trades, "Momentum", "win_rate", days=30)
    assert t["direction"] == "down"


def test_wr_trend_flat_when_no_closed_trades():
    t = dashboard._v2_trend([], "Funding", "win_rate", days=30)
    assert t["direction"] == "flat"


# ─── Per-bot filtering ─────────────────────────────────────────────────────

def test_trend_ignores_other_bots_trades():
    """A Whale trade should never affect Momentum's trend."""
    trades = [
        _trade("Momentum",  5, +10.0, "WIN"),    # Momentum: +$10 current
        _trade("Whale",    40, +999.0, "WIN"),   # Whale prior — must be ignored
        _trade("Whale",     5, -999.0, "LOSS"),  # Whale current — must be ignored
    ]
    t = dashboard._v2_trend(trades, "Momentum", "net_pnl", days=30)
    assert t["direction"] == "up"
    assert t["delta"] == pytest.approx(10.0)


# ─── Glyph rendering ───────────────────────────────────────────────────────

def test_trend_glyph_up_uses_up_arrow():
    assert dashboard._v2_trend_glyph("up") == "▲"


def test_trend_glyph_down_uses_down_arrow():
    assert dashboard._v2_trend_glyph("down") == "▼"


def test_trend_glyph_flat_uses_dash():
    assert dashboard._v2_trend_glyph("flat") == "—"


# ─── Render integration ────────────────────────────────────────────────────

def test_overview_renders_trend_chips_when_bot_has_trade_history():
    """When a bot has PnL + WR trend data, the bot card renders both chips."""
    jinja2 = pytest.importorskip("jinja2")
    from dashboard_renderer import render

    trades = [
        _trade("Momentum",  5, +30.0, "WIN"),
        _trade("Momentum", 10, +20.0, "WIN"),
        _trade("Momentum", 40, +30.0, "WIN"),
        _trade("Momentum", 50, -10.0, "LOSS"),
    ]
    html = render("base.html.j2", dashboard._v2_test_context(trades))
    assert 'class="trend-chip trend-chip--up"' in html
    assert 'aria-label="30-day Net PnL trend: up"' in html
    assert 'aria-label="30-day Win Rate trend' in html


def test_overview_hides_trend_chips_when_bot_has_no_history():
    """When a bot has zero trades, the trend chip is omitted (not 'flat')."""
    jinja2 = pytest.importorskip("jinja2")
    from dashboard_renderer import render

    html = render("base.html.j2", dashboard._v2_test_context([]))
    # No Momentum/Whale/Funding trades → no chips at all
    assert 'class="trend-chip' not in html
