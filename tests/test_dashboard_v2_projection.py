"""Phase D.1d — Projection tab tests.

Run: python -m pytest tests/test_dashboard_v2_projection.py -v
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


# ─── _v2_projection shaping ─────────────────────────────────────────────────

def test_projection_returns_headline_fields():
    p = dashboard._v2_projection()
    for k in ("rows", "starting_capital", "starting_capital_display",
              "live_notional", "live_notional_display",
              "total_annual", "total_annual_display",
              "annual_pct", "annual_pct_display",
              "total_trades_per_year", "total_trades_display"):
        assert k in p, f"projection missing {k!r}"


def test_projection_display_strings_are_pre_formatted():
    p = dashboard._v2_projection()
    # Starting capital should look like "$5,000" not "5000"
    assert p["starting_capital_display"].startswith("$")
    assert "," in p["starting_capital_display"] or p["starting_capital"] < 1000
    # PnL display is sign-aware
    assert p["total_annual_display"][0] in ("+", "−", "$")
    # Annual % has + or − sign
    assert p["annual_pct_display"][0] in ("+", "−", "0")


def test_projection_rows_each_have_display_fields():
    p = dashboard._v2_projection()
    if not p["rows"]:
        pytest.skip("no backtest stats configured — display fields untestable")
    for r in p["rows"]:
        for k in ("annual_pnl_live_display", "pf_display",
                  "annual_pct_display", "trades_per_year_display",
                  "dd_display"):
            assert k in r, f"row missing {k!r}: {r}"


# ─── Template render ───────────────────────────────────────────────────────

def _ctx():
    return {
        "operator": "ayott84", "env": "paper", "freshness": "0s",
        "build_sha": "abc12345", "build_ts": "2026-06-05 00:00 UTC",
        "bots": [
            {"class": "momentum", "monogram": "M", "name": "Momentum",
             "state": "live", "seen_label": "0s ago",
             "net_pnl": 0, "net_pnl_display": "$0.00",
             "trade_count": 0, "win_rate_display": "—"}
        ] * 3,
        "portfolio": {"net_pnl": 0, "net_pnl_display": "$0.00",
                      "closed_count": 0, "open_count": 0,
                      "win_rate_display": "—"},
        "trades":       [],
        "whale_meta":   dashboard._v2_whale_meta([]),
        "funding_meta": dashboard._v2_funding_meta([]),
        "breakout_meta": dashboard._v2_breakout_meta([]),
        "pair_meta":     dashboard._v2_pair_meta([]),
        "reversal_meta": dashboard._v2_reversal_meta([]),
        "projection":   dashboard._v2_projection(),
        "risk_metrics":      dashboard._v2_risk_metrics({}),
        "regime_expectancy": dashboard._v2_regime_expectancy({}),
    }


def test_projection_tab_renders_headline_kv_cells():
    html = render("base.html.j2", _ctx())
    for label in (
        "STARTING CAPITAL", "NOTIONAL", "PROJECTED ANNUAL PnL",
        "PROJECTED ANNUAL %", "TRADES / YEAR",
    ):
        assert label in html, f"{label} missing from projection tab"


def test_projection_tab_renders_per_strategy_table_or_empty_message():
    html = render("base.html.j2", _ctx())
    # Either real rows OR the empty-state message
    has_rows = "trades-table__strategy" in html and "<tbody>" in html
    has_empty_msg = "No strategies with backtest stats configured" in html
    assert has_rows or has_empty_msg


def test_projection_tab_includes_disclaimer_note():
    html = render("base.html.j2", _ctx())
    assert "Treat as" in html
    assert "doesn" in html.lower() or "doesn't" in html  # "past performance doesn't predict"
