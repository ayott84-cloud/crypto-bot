"""Integration smoke test for the Phase D V2 render path.

Renders the base template with a hand-built context and asserts the output
contains the expected real-data hooks (bot names, numbers, status pill
states). This catches template-vs-context drift before the bot deploys.

Run: python -m pytest tests/test_dashboard_v2_render.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

# Requires jinja2 — skip if not installed in the test env.
jinja2 = pytest.importorskip("jinja2")

from dashboard_renderer import render


def _ctx(net_pnl: float = -147.47):
    import dashboard
    bots = [
        {"class": "momentum", "monogram": "M", "name": "Momentum",
         "state": "live",   "seen_label": "0s ago",
         "net_pnl":  78.20, "net_pnl_display": "+$78.20",
         "trade_count": 17, "win_rate_display": "65%"},
        {"class": "whale",    "monogram": "W", "name": "Whale",
         "state": "dormant", "seen_label": "paused",
         "net_pnl": -225.67, "net_pnl_display": "−$225.67",
         "trade_count": 23,  "win_rate_display": "13%"},
        {"class": "funding",  "monogram": "F", "name": "Funding",
         "state": "live",    "seen_label": "33m ago",
         "net_pnl": 0.0,     "net_pnl_display": "$0.00",
         "trade_count": 0,   "win_rate_display": "—"},
    ]
    return {
        "operator":  "ayott84",
        "env":       "paper",
        "freshness": "0s",
        "build_sha": "abcd1234",
        "build_ts":  "2026-06-04 23:59 UTC",
        "bots": bots,
        # Tier 3.A: the Overview bot table renders from `mission`
        "mission": dashboard._v2_mission_control(bots, [], None),
        "portfolio": {
            "net_pnl":          net_pnl,
            "net_pnl_display":  "−$147.47" if net_pnl < 0 else "+$0.00",
            "closed_count":     40,
            "open_count":       0,
            "win_rate_display": "45.0%",
        },
        "trades":       [],
        "momentum_meta": dashboard._v2_momentum_meta(trades if "trades" in dir() else []),
        "whale_meta":   dashboard._v2_whale_meta([]),
        "funding_meta": dashboard._v2_funding_meta([]),
        "breakout_meta": dashboard._v2_breakout_meta([]),
        "pair_meta":     dashboard._v2_pair_meta([]),
        "reversal_meta":  dashboard._v2_reversal_meta([]),
        "scalp_meta":     dashboard._v2_scalp_meta([]),
        "crossover_meta": dashboard._v2_crossover_meta([]),
        "projection":   dashboard._v2_projection(),
        "bot_panels": {
            "momentum":  dashboard._v2_build_bot_panels([], None, "momentum"),
            "whale":     dashboard._v2_build_bot_panels([], None, "whale"),
            "funding":   dashboard._v2_build_bot_panels([], None, "funding"),
            "breakout":  dashboard._v2_build_bot_panels([], None, "breakout"),
            "pair":      dashboard._v2_build_bot_panels([], None, "pair"),
            "reversal":  dashboard._v2_build_bot_panels([], None, "reversal"),
            "scalp":     dashboard._v2_build_bot_panels([], None, "scalp"),
            "crossover": dashboard._v2_build_bot_panels([], None, "crossover"),
        },
        "risk_metrics":      dashboard._v2_risk_metrics({}),
        "regime_expectancy": dashboard._v2_regime_expectancy({}),
    }


def test_v2_render_produces_html_doc():
    html = render("base.html.j2", _ctx())
    assert html.startswith("<!DOCTYPE html>")
    assert "</html>" in html


def test_v2_render_inlines_tokens_css():
    html = render("base.html.j2", _ctx())
    assert "<link rel=" not in html      # all links inlined
    assert "--surface-0" in html         # tokens.css content embedded


def test_v2_render_inlines_dashboard_js():
    html = render("base.html.j2", _ctx())
    assert "<script src=" not in html
    assert "addEventListener" in html    # dashboard.js content embedded


def test_v2_render_shows_colophon_metadata():
    html = render("base.html.j2", _ctx())
    assert "AYOTT84" in html
    assert "PAPER" in html
    assert "ABCD1234" in html


def test_v2_render_shows_three_bot_cards_with_monograms():
    html = render("base.html.j2", _ctx())
    # Monograms appear inside .monogram spans
    assert "monogram--momentum" in html
    assert "monogram--whale" in html
    assert "monogram--funding" in html
    # Names
    assert ">Momentum<" in html
    assert ">Whale<" in html
    assert ">Funding<" in html


def test_v2_render_shows_status_pills_for_each_bot_state():
    html = render("base.html.j2", _ctx())
    assert "status-pill--live" in html
    assert "status-pill--dormant" in html


def test_v2_render_shows_pnl_with_sign_styling():
    html = render("base.html.j2", _ctx())
    # Positive momentum PnL gets .is-up class
    assert 'class="bot-card__pnl\n              is-up"' in html or "is-up" in html
    # Negative whale PnL gets .is-down
    assert "is-down" in html
    # Both displayed values are present
    assert "+$78.20" in html
    assert "−$225.67" in html


def test_v2_render_shows_portfolio_strip_with_corrected_net():
    html = render("base.html.j2", _ctx())
    assert "−$147.47" in html
    assert "NET PnL · all bots" in html or "NET PnL" in html


def test_v2_render_includes_placeholder_tabs():
    """Phase-D placeholders document migration progress on Trade Log/Whale/etc."""
    html = render("base.html.j2", _ctx())
    assert "tab-trades" in html
    assert "tab-projection" in html
    assert "tab-whale" in html
    assert "tab-funding" in html
    assert "Phase D" in html  # the placeholder note


def test_v2_render_tab_nav_is_a_real_tablist():
    html = render("base.html.j2", _ctx())
    assert 'role="tablist"' in html
    assert 'role="tab"' in html
    assert 'role="tabpanel"' in html
