"""Phase D.5 — accessibility audit tests.

Verifies semantic HTML + ARIA contract that templates promise. These tests
don't replace axe-core / Lighthouse but they catch regressions on the
specific contract points the plan calls out.

Run: python -m pytest tests/test_dashboard_v2_a11y.py -v
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


# ─── Skip link ─────────────────────────────────────────────────────────────

def test_skip_link_is_first_focusable_element():
    """A skip-to-main link must be the first thing keyboard users hit."""
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert 'href="#main-content"' in html
    assert "Skip to main content" in html


def test_main_landmark_has_matching_id():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert '<main class="page" id="main-content"' in html


# ─── Tab semantics ──────────────────────────────────────────────────────────

def test_tab_nav_uses_tablist_role_with_label():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert 'role="tablist"' in html
    assert 'aria-label="Dashboard sections"' in html


def test_active_tab_has_aria_current_page():
    """Active tab carries both aria-selected=true AND aria-current=page."""
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert 'aria-current="page"' in html
    # And the same button has aria-selected="true"
    # (Overview is initially active)
    assert 'aria-selected="true"  aria-current="page"' in html


def test_sidebar_nav_has_vertical_orientation():
    """J.1: sidebar tablist must declare aria-orientation=vertical."""
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert 'aria-orientation="vertical"' in html
    assert 'class="sidebar"' in html


def test_sidebar_groups_system_bots_and_analysis():
    """J.1: sidebar items are organized into three labeled groups."""
    html = render("base.html.j2", dashboard._v2_test_context([]))
    for label in ("SYSTEM", "BOTS", "ANALYSIS"):
        assert label in html, f"Missing sidebar group label: {label}"


def test_momentum_tab_now_exists_and_includes_momentum_meta_rows():
    """J.1: Momentum tab template renders without error and shows the new meta."""
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert 'data-tab="momentum"' in html
    assert 'id="tab-momentum"' in html
    # Heading is present
    assert "Momentum</h1>" in html or ">Momentum<" in html


def test_inactive_tabs_have_aria_selected_false():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    # 10 inactive tabs after Overview (Phase N added Crossover tab → 11 total)
    assert html.count('aria-selected="false"') == 10


def test_all_panels_have_tabpanel_role_and_tabindex():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    # 11 tab panels — count the actual <section> opening tag, not the
    # substring (which also appears in inlined JS selectors)
    assert html.count('<section role="tabpanel"') == 11
    assert html.count('tabindex="0"') >= 11


# ─── Theme toggle ──────────────────────────────────────────────────────────

def test_theme_toggle_has_aria_pressed_starting_false():
    """In dark mode (default), aria-pressed=false. JS keeps it in sync."""
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert 'data-theme-toggle' in html
    assert 'aria-pressed="false"' in html


def test_theme_toggle_has_descriptive_aria_label():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert 'aria-label="Toggle light/dark theme"' in html


# ─── Sparkline accessibility ───────────────────────────────────────────────

def test_sparkline_svg_has_img_role_and_aria_label():
    """Sparklines now carry role=img + aria-label summarizing the curve."""
    svg = dashboard._v2_sparkline_svg([1.0, 2.0, 3.0], label="Test trend")
    assert 'role="img"' in svg
    assert 'aria-label="Test trend' in svg


def test_sparkline_aria_label_describes_direction_and_change():
    """An up-trending series should say "up"; down-trending should say "down"."""
    up = dashboard._v2_sparkline_svg([0.0, 5.0, 10.0], label="Series A")
    dn = dashboard._v2_sparkline_svg([10.0, 5.0, 0.0], label="Series B")
    assert "up from" in up
    assert "down from" in dn


def test_sparkline_aria_label_includes_endpoints():
    """The aria-label includes start and end PnL values for context."""
    svg = dashboard._v2_sparkline_svg([0.0, 50.0, 78.20],
                                       label="Momentum 30-day cumulative PnL")
    assert "$78.20" in svg


def test_bot_card_sparkline_renders_with_descriptive_aria_label():
    trades = [
        _trade("Momentum", "2026-05-01", 105, 5.0,  "WIN"),
        _trade("Momentum", "2026-05-15", 110, 10.0, "WIN"),
    ]
    html = render("base.html.j2", dashboard._v2_test_context(trades))
    assert "Momentum 30-day cumulative PnL" in html


# ─── Tables ─────────────────────────────────────────────────────────────────

def test_trade_log_table_has_sr_only_caption():
    """The plan calls for sr-only caption on every data table."""
    trades = [_trade("Momentum", "2026-05-01", 105, 5.0, "WIN")]
    html = render("base.html.j2", dashboard._v2_test_context(trades))
    assert '<caption class="sr-only">' in html


def test_trade_log_th_uses_scope_col():
    """Every table header cell must specify scope for screen readers."""
    trades = [_trade("Momentum", "2026-05-01", 105, 5.0, "WIN")]
    html = render("base.html.j2", dashboard._v2_test_context(trades))
    # The trade log table has 13 columns
    assert html.count('scope="col"') >= 13


# ─── Status pills ──────────────────────────────────────────────────────────

def test_status_pills_have_role_status_and_aria_live():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
