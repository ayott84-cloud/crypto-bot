"""Phase D.2 — visual polish tests (theme toggle, sparklines).

Run: python -m pytest tests/test_dashboard_v2_polish.py -v
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


def _trade(bot, date_opened, exit_price, net_pnl, result):
    return {
        "id": 1, "date_opened": date_opened, "symbol": "BTCUSDT",
        "direction": "LONG", "strategy": "x", "bot": bot,
        "entry_price": 100, "exit_price": exit_price,
        "quantity": 1, "leverage": 10,
        "net_pnl": net_pnl, "result": result,
        "exit_reason": "",
    }


# ─── _v2_sparkline_points ───────────────────────────────────────────────────

def test_sparkline_points_empty_for_empty_trades():
    assert dashboard._v2_sparkline_points([]) == []


def test_sparkline_points_filters_to_closed_only():
    """An open position (exit_price=None) doesn't contribute to the curve."""
    trades = [_trade("Momentum", "2026-05-30", None, 0.0, "OPEN")]
    assert dashboard._v2_sparkline_points(trades, "Momentum") == []


def test_sparkline_points_aggregates_per_day():
    """Two same-day trades collapse to a single point."""
    trades = [
        _trade("Momentum", "2026-05-30", 105, 5.0, "WIN"),
        _trade("Momentum", "2026-05-30", 110, 10.0, "WIN"),
    ]
    pts = dashboard._v2_sparkline_points(trades, "Momentum", days=30)
    # The cumulative-PnL series ends at 15.0
    assert pts[-1] == 15.0


def test_sparkline_points_filters_by_bot_label():
    """Whale trades don't show up in the Momentum sparkline."""
    trades = [
        _trade("Momentum", "2026-05-30", 105, 5.0,  "WIN"),
        _trade("Whale",    "2026-05-30",  90, -10.0, "LOSS"),
    ]
    momentum = dashboard._v2_sparkline_points(trades, "Momentum")
    whale    = dashboard._v2_sparkline_points(trades, "Whale")
    assert momentum[-1] == 5.0
    assert whale[-1] == -10.0


def test_sparkline_points_portfolio_sums_all_bots_when_label_none():
    trades = [
        _trade("Momentum", "2026-05-30", 105, 5.0,  "WIN"),
        _trade("Whale",    "2026-05-30",  90, -10.0, "LOSS"),
    ]
    portfolio = dashboard._v2_sparkline_points(trades, None)
    assert portfolio[-1] == -5.0


def test_sparkline_points_cumulative_runs_chronologically():
    """Values should accumulate monotonically across the time axis."""
    trades = [
        _trade("Momentum", "2026-05-01", 105, 5.0,  "WIN"),
        _trade("Momentum", "2026-05-15", 110, 10.0, "WIN"),
        _trade("Momentum", "2026-05-30",  95, -3.0, "LOSS"),
    ]
    pts = dashboard._v2_sparkline_points(trades, "Momentum", days=60)
    # End value = 5 + 10 - 3 = 12
    assert pts[-1] == 12.0
    # Must be monotonically increasing then decreasing somewhere
    assert max(pts) == 15.0


# ─── _v2_sparkline_svg ──────────────────────────────────────────────────────

def test_sparkline_svg_returns_empty_for_zero_points():
    assert dashboard._v2_sparkline_svg([]) == ""


def test_sparkline_svg_returns_empty_for_single_point():
    """A single point cannot form a polyline; renderer should skip."""
    assert dashboard._v2_sparkline_svg([5.0]) == ""


def test_sparkline_svg_renders_polyline_for_two_or_more_points():
    svg = dashboard._v2_sparkline_svg([1.0, 2.0, 3.0])
    assert svg.startswith("<svg")
    assert "polyline" in svg
    assert "spark__line" in svg


def test_sparkline_svg_renders_zero_axis_when_data_crosses_zero():
    """A dashed zero line should appear when min<0<max."""
    svg = dashboard._v2_sparkline_svg([-3.0, 0.0, 2.0])
    assert "spark__zero" in svg


def test_sparkline_svg_omits_zero_axis_when_all_positive():
    svg = dashboard._v2_sparkline_svg([1.0, 2.0, 3.0])
    assert "spark__zero" not in svg


def test_sparkline_svg_omits_zero_axis_when_all_negative():
    svg = dashboard._v2_sparkline_svg([-3.0, -2.0, -1.0])
    assert "spark__zero" not in svg


def test_sparkline_svg_accepts_custom_stroke_class():
    svg = dashboard._v2_sparkline_svg([1.0, 2.0],
                                       stroke_class="spark__line spark__line--whale")
    assert "spark__line--whale" in svg


# ─── Theme toggle in colophon ───────────────────────────────────────────────

def test_colophon_renders_theme_toggle_button():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert "theme-toggle" in html
    assert "data-theme-toggle" in html
    assert 'aria-label="Toggle' in html


def test_colophon_renders_both_dark_and_light_icons_for_css_swap():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert "theme-toggle__icon--dark" in html
    assert "theme-toggle__icon--light" in html


def test_root_element_starts_with_dark_theme_data_attribute():
    """Pre-toggle default is dark; JS reads localStorage and may flip post-load."""
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert 'data-theme="dark"' in html


# ─── Bot card sparkline render ──────────────────────────────────────────────

def test_bot_card_renders_spark_svg_when_present():
    trades = [
        _trade("Momentum", "2026-05-01", 105, 5.0,  "WIN"),
        _trade("Momentum", "2026-05-15", 110, 10.0, "WIN"),
    ]
    html = render("base.html.j2", dashboard._v2_test_context(trades))
    assert "bot-card__spark" in html
    # SVG inlined directly
    assert "<svg" in html
    assert "spark__line--momentum" in html


def test_bot_card_omits_spark_when_no_data():
    """No bot-card spark <div> when there's no trade data.

    The class names appear in the inlined CSS as selectors. To distinguish
    rendered markup from CSS, check for the actual <div ... > tag and for
    the <polyline class="..."> attribute (which only appears in real SVGs).
    """
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert '<div class="bot-card__spark"' not in html
    assert '<polyline class="spark__line spark__line--momentum"' not in html
    assert '<polyline class="spark__line spark__line--whale"'    not in html
    assert '<polyline class="spark__line spark__line--funding"'  not in html


def test_portfolio_strip_renders_spark_when_data_present():
    trades = [
        _trade("Momentum", "2026-05-01", 105, 5.0,  "WIN"),
        _trade("Whale",    "2026-05-15",  90, -10.0, "LOSS"),
    ]
    html = render("base.html.j2", dashboard._v2_test_context(trades))
    assert "portfolio-strip__spark" in html
