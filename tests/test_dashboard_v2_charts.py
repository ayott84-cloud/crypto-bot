"""Phase D.7e — Overview cumulative P/L equity curve + daily P/L bars.

Two new panels land below the bot cards on the Overview tab:
  - Equity curve: 90-day cumulative P/L with 4 overlaid series
    (Portfolio aggregate + Momentum + Whale + Funding).
  - Daily bars: last 30 days, green up / red down.

Run: python -m pytest tests/test_dashboard_v2_charts.py -v
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


def _trade(bot, days_ago, net_pnl):
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "id": 1,
        "date_opened": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "date_closed": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": "BTCUSDT", "direction": "LONG", "strategy": "x",
        "bot": bot, "entry_price": 100,
        "exit_price": 105 if net_pnl > 0 else 95,
        "quantity": 1, "leverage": 10,
        "net_pnl": net_pnl,
        "result": "WIN" if net_pnl > 0 else "LOSS",
        "exit_reason": "",
    }


# ─── _v2_equity_series ─────────────────────────────────────────────────────

def test_equity_series_returns_four_series_with_labels():
    data = dashboard._v2_equity_series([], days=90)
    assert "labels" in data and "series" in data
    assert len(data["labels"]) == 90
    series_names = [s["label"] for s in data["series"]]
    assert series_names == ["Portfolio", "Momentum", "Whale", "Funding"]


def test_equity_series_aggregates_all_bots_in_portfolio_line():
    trades = [
        _trade("Momentum", 5, +30.0),
        _trade("Whale",    5, -10.0),
        _trade("Funding",  5, +5.0),
    ]
    data = dashboard._v2_equity_series(trades, days=90)
    portfolio = next(s for s in data["series"] if s["label"] == "Portfolio")
    # Cumulative ends at +30 - 10 + 5 = +25
    assert portfolio["values"][-1] == pytest.approx(25.0)


def test_equity_series_per_bot_lines_only_include_their_trades():
    trades = [
        _trade("Momentum", 5, +30.0),
        _trade("Whale",    5, -10.0),
    ]
    data = dashboard._v2_equity_series(trades, days=90)
    mom = next(s for s in data["series"] if s["label"] == "Momentum")
    whl = next(s for s in data["series"] if s["label"] == "Whale")
    assert mom["values"][-1] == pytest.approx(30.0)
    assert whl["values"][-1] == pytest.approx(-10.0)


def test_equity_series_carries_zero_for_days_with_no_trades():
    trades = [_trade("Momentum", 5, +30.0)]
    data = dashboard._v2_equity_series(trades, days=90)
    mom = next(s for s in data["series"] if s["label"] == "Momentum")
    # The first 85 days are pre-trade → cumulative still 0
    assert mom["values"][0] == 0.0


def test_equity_series_has_css_modifier_per_bot():
    """Each series exposes a `modifier` to drive CSS color (aggregate/momentum/..)."""
    data = dashboard._v2_equity_series([], days=90)
    mods = {s["label"]: s["modifier"] for s in data["series"]}
    assert mods == {
        "Portfolio": "aggregate",
        "Momentum":  "momentum",
        "Whale":     "whale",
        "Funding":   "funding",
    }


# ─── _v2_equity_curve_svg ──────────────────────────────────────────────────

def test_equity_curve_svg_empty_when_no_trades():
    data = dashboard._v2_equity_series([], days=90)
    svg = dashboard._v2_equity_curve_svg(data)
    # All-zero series should still render the zero baseline + 4 polylines
    # at y=zero. Confirming we always emit a valid SVG, not "".
    assert svg.startswith("<svg") and svg.endswith("</svg>")


def test_equity_curve_svg_renders_four_polylines():
    trades = [_trade("Momentum", 5, +30.0)]
    data = dashboard._v2_equity_series(trades, days=90)
    svg = dashboard._v2_equity_curve_svg(data)
    assert svg.count("<polyline") == 4


def test_equity_curve_svg_emits_role_img_and_aria_label():
    data = dashboard._v2_equity_series([_trade("Momentum", 5, +10.0)], days=90)
    svg = dashboard._v2_equity_curve_svg(data)
    assert 'role="img"' in svg
    assert 'aria-label="' in svg
    assert "cumulative PnL curves" in svg


def test_equity_curve_svg_includes_zero_baseline():
    data = dashboard._v2_equity_series([_trade("Momentum", 5, +10.0)], days=90)
    svg = dashboard._v2_equity_curve_svg(data)
    assert "equity-curve__zero" in svg


def test_equity_series_trims_leading_zero_only_days():
    """The chart should start at the first day any bot has a trade — not
    waste horizontal space on a 60-day flat-line lead-in."""
    trades = [_trade("Momentum", 5, +30.0)]  # only one trade, 5 days ago
    data = dashboard._v2_equity_series(trades, days=90)
    # Before trim: 90 labels with first ~85 days all zero
    # After trim: fewer labels, starting close to the first trade
    assert len(data["labels"]) < 30, (
        f"Expected leading zero days trimmed, got {len(data['labels'])} labels"
    )
    # And the portfolio series should reach +30
    portfolio = next(s for s in data["series"] if s["label"] == "Portfolio")
    assert portfolio["values"][-1] == pytest.approx(30.0)


def test_equity_series_no_trim_when_data_starts_early():
    """When trades exist back to day 60+, the full window renders."""
    trades = [_trade("Momentum", 80, +10.0)]
    data = dashboard._v2_equity_series(trades, days=90)
    assert len(data["labels"]) >= 80


def test_equity_series_empty_input_keeps_full_window():
    """Zero trades → don't crash, return full window of zeros."""
    data = dashboard._v2_equity_series([], days=90)
    assert len(data["labels"]) == 90


def test_equity_curve_svg_uses_per_bot_class_modifiers():
    data = dashboard._v2_equity_series([_trade("Momentum", 5, +10.0)], days=90)
    svg = dashboard._v2_equity_curve_svg(data)
    assert "equity-curve__series--aggregate" in svg
    assert "equity-curve__series--momentum"  in svg
    assert "equity-curve__series--whale"     in svg
    assert "equity-curve__series--funding"   in svg


# ─── _v2_daily_pnl_bars ────────────────────────────────────────────────────

def test_daily_pnl_bars_returns_exactly_n_days():
    bars = dashboard._v2_daily_pnl_bars([], days=30)
    assert len(bars) == 30


def test_daily_pnl_bars_aggregates_all_bots_per_day():
    trades = [
        _trade("Momentum", 5, +30.0),
        _trade("Whale",    5, -10.0),
        _trade("Funding",  5, +5.0),
    ]
    bars = dashboard._v2_daily_pnl_bars(trades, days=30)
    # The 6th-from-end bar (index 30-1-5=24) should sum to +25
    assert any(b["pnl"] == pytest.approx(25.0) for b in bars)


def test_daily_pnl_bars_zero_pnl_for_quiet_days():
    bars = dashboard._v2_daily_pnl_bars([], days=30)
    assert all(b["pnl"] == 0.0 for b in bars)


# ─── _v2_daily_pnl_svg ─────────────────────────────────────────────────────

def test_daily_pnl_svg_empty_input_returns_empty_string():
    assert dashboard._v2_daily_pnl_svg([]) == ""


def test_daily_pnl_svg_renders_rect_per_day():
    bars = [{"date": "2026-06-01", "pnl": +5.0},
            {"date": "2026-06-02", "pnl": -3.0},
            {"date": "2026-06-03", "pnl": +1.0}]
    svg = dashboard._v2_daily_pnl_svg(bars)
    assert svg.count("<rect") == 3


def test_daily_pnl_svg_uses_up_class_for_positive_days():
    bars = [{"date": "2026-06-01", "pnl": +5.0}]
    svg = dashboard._v2_daily_pnl_svg(bars)
    assert "daily-bar--up" in svg


def test_daily_pnl_svg_uses_down_class_for_negative_days():
    bars = [{"date": "2026-06-01", "pnl": -5.0}]
    svg = dashboard._v2_daily_pnl_svg(bars)
    assert "daily-bar--down" in svg


def test_daily_pnl_svg_has_role_img_and_aria_label():
    bars = [{"date": "2026-06-01", "pnl": +5.0}]
    svg = dashboard._v2_daily_pnl_svg(bars)
    assert 'role="img"' in svg
    assert 'aria-label="' in svg


# ─── Axis labels (Phase D.7h) ──────────────────────────────────────────────

def test_equity_curve_svg_renders_y_axis_dollar_labels():
    """Y-axis must show the high and low PnL values for the curve."""
    trades = [
        _trade("Momentum",  5, +100.0),
        _trade("Momentum", 40, -50.0),
    ]
    data = dashboard._v2_equity_series(trades, days=90)
    svg = dashboard._v2_equity_curve_svg(data)
    # Y-axis labels are <text class="chart-axis chart-axis--y">
    assert 'class="chart-axis chart-axis--y"' in svg
    # Should show dollar-formatted values (e.g. "$100" or "+$100")
    assert "$" in svg


def test_equity_curve_svg_renders_x_axis_date_labels():
    """X-axis must show the start, middle, and end date of the window."""
    trades = [_trade("Momentum", 5, +30.0)]
    data = dashboard._v2_equity_series(trades, days=90)
    svg = dashboard._v2_equity_curve_svg(data)
    assert 'class="chart-axis chart-axis--x"' in svg
    # The first and last labels in the window should appear (as MM-DD or full date)
    first_label = data["labels"][0]  # e.g. "2026-03-07"
    last_label  = data["labels"][-1]
    # Render in MM-DD style — confirm at least the day component is present
    assert first_label[-5:] in svg or first_label in svg
    assert last_label[-5:]  in svg or last_label  in svg


def test_equity_curve_svg_includes_zero_y_label_when_zero_in_range():
    """When the data crosses zero, mark $0 on the Y-axis."""
    trades = [
        _trade("Momentum", 5,  +50.0),
        _trade("Momentum", 6,  -50.0),
    ]
    data = dashboard._v2_equity_series(trades, days=90)
    svg = dashboard._v2_equity_curve_svg(data)
    assert ">$0<" in svg


def test_daily_pnl_svg_renders_y_axis_max_label():
    """Daily bars need a max-value label so operator knows scale."""
    bars = [
        {"date": "2026-06-01", "pnl": +25.0},
        {"date": "2026-06-02", "pnl": -10.0},
    ]
    svg = dashboard._v2_daily_pnl_svg(bars)
    assert 'class="chart-axis chart-axis--y"' in svg
    assert "$" in svg


def test_daily_pnl_svg_renders_x_axis_first_and_last_dates():
    bars = [
        {"date": "2026-06-01", "pnl": +5.0},
        {"date": "2026-06-15", "pnl": -3.0},
        {"date": "2026-06-30", "pnl": +1.0},
    ]
    svg = dashboard._v2_daily_pnl_svg(bars)
    assert 'class="chart-axis chart-axis--x"' in svg
    # First + last date components should appear
    assert "06-01" in svg
    assert "06-30" in svg


# ─── Overview render integration ───────────────────────────────────────────

def test_overview_renders_equity_curve_panel():
    jinja2 = pytest.importorskip("jinja2")
    from dashboard_renderer import render
    trades = [_trade("Momentum", 5, +30.0)]
    html = render("base.html.j2", dashboard._v2_test_context(trades))
    assert "chart-panel equity-curve" in html
    assert "Cumulative P/L · 90 days" in html
    assert "<polyline class=\"equity-curve__series" in html


def test_overview_renders_daily_pnl_panel():
    jinja2 = pytest.importorskip("jinja2")
    from dashboard_renderer import render
    trades = [_trade("Momentum", 5, +30.0)]
    html = render("base.html.j2", dashboard._v2_test_context(trades))
    assert "chart-panel daily-pnl" in html
    assert "Daily P/L · 30 days" in html
    assert "daily-bar--up" in html


def test_overview_chart_legend_lists_all_four_bots():
    jinja2 = pytest.importorskip("jinja2")
    from dashboard_renderer import render
    html = render("base.html.j2", dashboard._v2_test_context([]))
    # Legend dot+label per series
    for label in ("Portfolio", "Momentum", "Whale", "Funding"):
        assert label in html
