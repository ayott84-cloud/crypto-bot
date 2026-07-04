"""Dashboard Tier 1.1 + 1.2 (Jul 2026 redesign options doc).

1.1 Kill-switch status panel: the P3.6 breaker fired live on Jul 4 and
    the only evidence was journalctl. The Overview must answer "am I
    safe?" — per-owner armed/tripped state + the 24h drawdown meter
    against the effective threshold.
1.2 Live bracket columns: P5a persists sl_price/tp_price on positions
    at entry; the positions panel shows the ACTUAL exchange-resident
    brackets (em-dash for legacy positions without the fields).

Run: python -m pytest tests/test_dashboard_killswitch_panel.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

import dashboard
from dashboard_renderer import render


# ─── Tier 1.1 — kill-switch panel context ──────────────────────────────────

_OWNERS = {"momentum", "whale", "funding", "scalp", "crossover",
            "breakout", "pair", "reversal"}


def test_kill_switch_panel_shape():
    panel = dashboard._v2_kill_switch_panel()
    assert panel["available"] is True
    assert {o["owner"] for o in panel["owners"]} == _OWNERS
    for o in panel["owners"]:
        assert o["state_label"] in ("ARMED", "TRIPPED")
        assert "reason" in o
    daily = panel["daily"]
    assert daily["threshold_display"].startswith("-$")
    assert "pnl_display" in daily
    assert isinstance(daily["breached"], bool)
    assert 0 <= daily["pct_used"] <= 100


def test_kill_switch_panel_in_context_and_renders():
    ctx = dashboard._v2_test_context()
    assert "kill_switch" in ctx
    html = render("base.html.j2", ctx)
    assert "Kill switches" in html
    assert "24h drawdown" in html


def test_kill_switch_panel_never_raises(monkeypatch):
    """Watchdog philosophy: a broken journal must degrade the panel,
    not the dashboard build."""
    import kill_switch as ks
    def boom():
        raise RuntimeError("journal exploded")
    monkeypatch.setattr(ks, "status_summary", boom)
    panel = dashboard._v2_kill_switch_panel()
    assert panel["available"] is False


# ─── Tier 1.2 — bracket columns on open positions ──────────────────────────

def _state_with(pos):
    return {"positions": {"SCALP_ETH_5M": pos}}


def test_positions_panel_shows_persisted_brackets():
    pos = {"symbol": "ETHUSDT", "direction": "LONG", "entry_price": 2500.0,
            "quantity": 0.04, "strategy": "ETH 15m Scalp",
            "sl_price": 2450.5, "tp_price": 2574.25, "bracket_kind": "atr"}
    rows = dashboard._v2_open_positions_for_bot(_state_with(pos), "scalp")
    assert len(rows) == 1
    assert rows[0]["sl_display"] == "2,450.50"
    assert rows[0]["tp_display"] == "2,574.25"


def test_positions_panel_dashes_for_legacy_positions():
    pos = {"symbol": "ETHUSDT", "direction": "LONG", "entry_price": 2500.0,
            "quantity": 0.04, "strategy": "ETH 15m Scalp"}
    rows = dashboard._v2_open_positions_for_bot(_state_with(pos), "scalp")
    assert rows[0]["sl_display"] == "—"
    assert rows[0]["tp_display"] == "—"


def test_positions_panel_dash_for_invalidation_no_tp():
    """Crossover invalidation mode has sl but deliberately no TP."""
    pos = {"symbol": "ETHUSDT", "direction": "LONG", "entry_price": 2500.0,
            "quantity": 0.04, "strategy": "ETH 1h Crossover",
            "sl_price": 2400.0, "tp_price": None,
            "exit_kind": "invalidation"}
    rows = dashboard._v2_open_positions_for_bot(
        {"positions": {"CROSSOVER_ETH_1H": pos}}, "crossover")
    assert rows[0]["sl_display"] == "2,400.00"
    assert rows[0]["tp_display"] == "—"


def test_positions_template_renders_bracket_columns():
    pos = {"symbol": "ETHUSDT", "direction": "LONG", "entry_price": 2500.0,
            "quantity": 0.04, "strategy": "ETH 15m Scalp",
            "sl_price": 2450.5, "tp_price": 2574.25}
    rows = dashboard._v2_open_positions_for_bot(_state_with(pos), "scalp")
    html = render("components/bot_positions_panel.html.j2",
                    {"positions": rows})
    assert "<th scope=\"col\">SL</th>" in html
    assert "<th scope=\"col\">TP</th>" in html
    assert "2,450.50" in html and "2,574.25" in html
