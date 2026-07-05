"""Three dashboard gaps from the Jul 5 fleet synopsis:

1. Momentum projection rows must cite the HONEST long-window stats
   (17000-bar Coinbase replay), not the 5.3yr TradingView-era numbers.
2. Momentum tab shows its demoted configs as a candidates table
   (scalp/breakout already had one).
3. A Routines panel surfaces the scheduled processes' last-run health
   (risk sentinel / alpha brief / prediction scan / Discord daemon)
   via lightweight run-stamps — timers report to Discord, but the
   dashboard should show they're alive.

Run: python -m pytest tests/test_dashboard_gap_fixes.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

import dashboard
from dashboard_renderer import render


# ─── Gap 1: honest momentum projection stats ───────────────────────────────

def test_momentum_kept_configs_carry_honest_stats():
    from config import ASSETS, BACKTEST_YEARS
    for key in ASSETS:
        stats = ASSETS[key].get("backtest_stats")
        assert stats, f"{key} missing backtest_stats"
        assert "honest replay" in stats.get("source", ""), key
        assert stats.get("years") != BACKTEST_YEARS, key
    assert ASSETS["BTC"]["backtest_stats"]["pf"] == pytest.approx(1.57)
    assert ASSETS["ARB_4H"]["backtest_stats"]["pf"] == pytest.approx(1.81)


def test_projection_momentum_rows_use_honest_windows():
    proj = dashboard._compute_yearly_projection()
    mom = {r["key"]: r for r in proj["rows"] if r["bot"] == "Momentum"}
    assert "BTC" in mom
    assert mom["BTC"]["window_years"] == pytest.approx(7.8)


# ─── Gap 2: momentum candidates table ──────────────────────────────────────

def test_momentum_meta_exposes_demoted_candidates():
    from config import MOMENTUM_CANDIDATE_ASSETS
    meta = dashboard._v2_momentum_meta([])
    assert "candidate_assets" in meta
    assert len(meta["candidate_assets"]) == len(MOMENTUM_CANDIDATE_ASSETS)


def test_momentum_tab_renders_candidates_section():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert "MOMENTUM_CANDIDATE_ASSETS" in html


# ─── Gap 3: routines panel ─────────────────────────────────────────────────

def test_routine_stamps_roundtrip(tmp_path, monkeypatch):
    import routine_stamps as rs
    monkeypatch.setattr(rs, "_STAMPS_PATH", tmp_path / "stamps.json")
    rs.stamp("risk_check")
    stamps = rs.read_stamps()
    assert "risk_check" in stamps
    # parses as an aware ISO timestamp
    datetime.fromisoformat(stamps["risk_check"])


def test_routines_panel_classifies_freshness(monkeypatch):
    import routine_stamps as rs
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(minutes=20)).isoformat()
    stale = (now - timedelta(hours=9)).isoformat()
    monkeypatch.setattr(rs, "read_stamps", lambda: {
        "risk_check": stale,          # hourly cadence → 9h is stale
        "prediction_scan": fresh,     # daily cadence → fresh
    })
    panel = dashboard._v2_routines_panel()
    assert panel["available"] is True
    rows = {r["name"]: r for r in panel["rows"]}
    assert rows["risk_check"]["stale"] is True
    assert rows["prediction_scan"]["stale"] is False
    assert rows["alpha_brief"]["last_display"] == "never"


def test_routines_panel_renders():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert "Routines" in html
    assert "Risk sentinel" in html
