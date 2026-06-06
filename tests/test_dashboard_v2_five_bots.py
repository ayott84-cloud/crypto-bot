"""Phase 2A — five-bot Overview layout.

After Phase F + G the fleet has 5 bots. The Overview tab now renders
all five as bot cards. This test pins that contract so future phases
don't accidentally drop one when refactoring.

Run: python -m pytest tests/test_dashboard_v2_five_bots.py -v
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


def test_overview_renders_all_five_bot_cards():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    for name in ("Momentum", "Whale", "Funding", "Breakout", "Pair"):
        assert f">{name}<" in html, f"Missing bot card: {name}"


def test_overview_renders_all_five_monograms():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    for css_class in ("monogram--momentum", "monogram--whale",
                       "monogram--funding", "monogram--breakout",
                       "monogram--pair"):
        assert css_class in html, f"Missing CSS class: {css_class}"


def test_breakout_card_shows_paused_when_breakout_paused_true(monkeypatch):
    import breakout_config
    monkeypatch.setattr(breakout_config, "BREAKOUT_PAUSED", True)
    ctx = dashboard._v2_test_context([])
    # The bot card's state should be 'dormant' and label 'paused'
    breakout_card = next(b for b in ctx["bots"] if b["class"] == "breakout")
    assert breakout_card["state"] == "dormant"
    assert "paused" in breakout_card["seen_label"].lower()


def test_pair_card_shows_paused_when_pair_paused_true(monkeypatch):
    import pair_config
    monkeypatch.setattr(pair_config, "PAIR_PAUSED", True)
    ctx = dashboard._v2_test_context([])
    pair_card = next(b for b in ctx["bots"] if b["class"] == "pair")
    assert pair_card["state"] == "dormant"


def test_breakout_why_panel_explains_paused_state():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    # Should include the breakout-specific why-silent copy
    assert "pending backtest validation" in html or "BREAKOUT_PAUSED" in html


def test_pair_why_panel_explains_paused_state():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert "PAIR_PAUSED" in html or "z-score backtest" in html


def test_compute_bot_status_returns_all_five_keys(tmp_path, monkeypatch):
    """_compute_bot_status() exposes a status entry for each of the 5 bots."""
    # Point BOT_DIR at an empty tmp_path so every heartbeat returns NEVER
    monkeypatch.setattr(dashboard, "BOT_DIR", tmp_path)
    # Recreate the function's bot_dir via monkeypatched Path(__file__)
    # The function reads from Path(__file__).resolve().parent so we can't
    # easily redirect; just verify the keys exist in the live call.
    status = dashboard._compute_bot_status()
    for key in ("momentum", "whale", "funding", "breakout", "pair"):
        assert key in status, f"Missing bot status: {key}"
