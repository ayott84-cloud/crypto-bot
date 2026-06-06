"""Phase 2C.1 — Breakout / Pair / Reversal operator tab content.

Renders each new tab and asserts on the key config rows + state banner.

Run: python -m pytest tests/test_dashboard_v2_new_tabs.py -v
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


# ─── Breakout tab ──────────────────────────────────────────────────────────

def test_breakout_meta_exposes_default_thresholds():
    meta = dashboard._v2_breakout_meta([])
    assert meta["donchian_period"] > 0
    assert meta["adx_threshold"] > 0
    assert meta["sl_atr_mult"] > 0
    assert meta["assets"]  # at least one asset configured


def test_breakout_meta_notional_is_margin_times_leverage():
    meta = dashboard._v2_breakout_meta([])
    assert meta["notional_usd"] == meta["margin_usd"] * meta["leverage"]


def test_breakout_tab_renders_paused_banner_when_paused(monkeypatch):
    import breakout_config
    monkeypatch.setattr(breakout_config, "BREAKOUT_PAUSED", True)
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert "BREAKOUT_PAUSED=true" in html


def test_breakout_tab_renders_config_rows():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    for label in ("Donchian entry period", "Donchian exit period",
                   "ADX threshold", "SL distance", "Sizing"):
        assert label in html, f"breakout row missing: {label}"


def test_breakout_tab_renders_asset_table():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert "Configured assets" in html


# ─── Pair tab ──────────────────────────────────────────────────────────────

def test_pair_meta_exposes_pair_symbols_and_z_params():
    meta = dashboard._v2_pair_meta([])
    assert meta["long_symbol"]
    assert meta["short_symbol"]
    assert meta["z_window"] > 0
    assert meta["entry_z"] > 0
    assert meta["exit_z"] >= 0


def test_pair_meta_includes_open_position_when_state_supplied():
    state = {"positions": {"PAIR_ETHBTC_LONG_LEG": {
        "direction": "LONG", "entry_ratio": 0.05,
        "entry_z": -2.3, "bars_held": 2,
    }}}
    meta = dashboard._v2_pair_meta([], state=state)
    assert meta["open_position"]["direction"] == "LONG"
    assert meta["open_position"]["bars_held"] == 2


def test_pair_tab_renders_paused_banner_when_paused(monkeypatch):
    import pair_config
    monkeypatch.setattr(pair_config, "PAIR_PAUSED", True)
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert "PAIR_PAUSED=true" in html


def test_pair_tab_renders_config_rows():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    for label in ("Pair", "Z-score window", "Entry threshold",
                   "Exit threshold", "Max hold"):
        assert label in html, f"pair row missing: {label}"


# ─── Reversal tab ──────────────────────────────────────────────────────────

def test_reversal_meta_exposes_rsi_and_range_params():
    meta = dashboard._v2_reversal_meta([])
    assert meta["rsi_length"] > 0
    assert meta["oversold"] >= 0
    assert meta["overbought"] <= 100
    assert meta["range_mult"] > 0
    assert meta["assets"]


def test_reversal_tab_renders_paused_banner_when_paused(monkeypatch):
    import reversal_config
    monkeypatch.setattr(reversal_config, "REVERSAL_PAUSED", True)
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert "REVERSAL_PAUSED=true" in html


def test_reversal_tab_renders_config_rows():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    for label in ("RSI length", "Oversold / overbought",
                   "Range multiplier", "Dot polarity threshold",
                   "Time stop"):
        assert label in html, f"reversal row missing: {label}"


# ─── Tab nav buttons ──────────────────────────────────────────────────────

def test_tab_nav_includes_all_three_new_bot_tabs():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    for tab_text in ("Breakout", "Pair", "Reversal"):
        assert f">{tab_text}<" in html
