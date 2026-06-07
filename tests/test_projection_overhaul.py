"""Phase J.6 — projection overhaul tests.

The legacy `_compute_yearly_projection` only iterated `config.ASSETS`
(momentum) and appended one Whale row. J.6 widens it to include
Breakout + Pair backtest stats and a placeholder Funding row, with
each row carrying a `confidence` tag derived from trade count and a
per-row `window_years` so the template can show the sample-size
honestly.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

import dashboard


# ─── _confidence_tag ──────────────────────────────────────────────────────

def test_confidence_tag_thresholds():
    assert dashboard._confidence_tag(0)   == "none"
    assert dashboard._confidence_tag(1)   == "low"
    assert dashboard._confidence_tag(19)  == "low"
    assert dashboard._confidence_tag(20)  == "med"
    assert dashboard._confidence_tag(49)  == "med"
    assert dashboard._confidence_tag(50)  == "high"
    assert dashboard._confidence_tag(500) == "high"


# ─── _compute_yearly_projection — bot coverage ───────────────────────────

def test_projection_includes_momentum_whale_breakout_pair():
    """Every bot with backtest stats should produce at least one row."""
    proj = dashboard._compute_yearly_projection()
    bots = {r["bot"] for r in proj["rows"]}
    # Momentum should always be present (16+ ASSETS configs)
    assert "Momentum" in bots
    # Whale row is always emitted when whale_config has stats
    assert "Whale" in bots
    # Breakout has BREAKOUT_BACKTEST_STATS now (J.6)
    assert "Breakout" in bots
    # Pair has PAIR_BACKTEST_STATS now (J.6)
    assert "Pair" in bots


def test_projection_funding_row_is_awaiting_data():
    """Funding has no backtest stats yet — placeholder row marked is_awaiting."""
    proj = dashboard._compute_yearly_projection()
    funding_rows = [r for r in proj["rows"] if r["bot"] == "Funding"]
    if not funding_rows:
        pytest.skip("funding_config not importable; row skipped intentionally")
    assert len(funding_rows) == 1
    assert funding_rows[0].get("is_awaiting") is True
    assert funding_rows[0]["confidence"] == "none"


def test_projection_whale_row_surfaces_win_rate():
    """Regression: whale_config uses "win_rate" key, others use "wr".
    _project_row must accept both or Whale row silently shows 0% WR.
    Bug caught in J.6 peer review."""
    proj = dashboard._compute_yearly_projection()
    whale_rows = [r for r in proj["rows"] if r["bot"] == "Whale"]
    if not whale_rows:
        pytest.skip("whale_config not importable")
    # WHALE_BACKTEST_STATS has win_rate=40.4
    assert whale_rows[0]["wr"] > 0, "Whale row dropped win_rate to 0"
    assert abs(whale_rows[0]["wr"] - 40.4) < 0.1


def test_projection_each_row_has_required_keys():
    """Every projection row must surface enough fields for the template."""
    proj = dashboard._compute_yearly_projection()
    required = {"bot", "name", "symbol", "interval", "pf", "trades_per_year",
                "annual_pnl_live", "dd_pct", "window_years", "confidence"}
    for r in proj["rows"]:
        missing = required - set(r.keys())
        assert not missing, f"row {r.get('key')} missing {missing}"


def test_projection_rows_sorted_by_annual_pnl_desc():
    proj = dashboard._compute_yearly_projection()
    rows = proj["rows"]
    if len(rows) < 2:
        pytest.skip("need at least 2 rows to verify sort")
    for i in range(len(rows) - 1):
        a = rows[i].get("annual_pnl_live", 0)
        b = rows[i + 1].get("annual_pnl_live", 0)
        assert a >= b, (
            f"projection row {i} ({rows[i]['key']}) PnL {a} "
            f"< row {i+1} ({rows[i+1]['key']}) PnL {b}")


# ─── Per-row window_years (no shared global divisor) ─────────────────────

def test_projection_breakout_uses_own_window_not_global():
    """Each breakout row uses its OWN window, not the global BACKTEST_YEARS.
    4H assets ≈ 0.46yr; 1H assets ≈ 0.11yr; mixing TFs is expected."""
    proj = dashboard._compute_yearly_projection()
    breakout_rows = [r for r in proj["rows"] if r["bot"] == "Breakout"]
    if not breakout_rows:
        pytest.skip("breakout_config not importable")
    # Every row should be well below the global 5.3yr divisor — the
    # whole point is they have their OWN per-row windows.
    for r in breakout_rows:
        assert 0 < r["window_years"] < 5.0, (
            f"breakout {r['key']} window {r['window_years']} suggests "
            "the global BACKTEST_YEARS divisor leaked in")


def test_projection_pair_uses_own_window():
    proj = dashboard._compute_yearly_projection()
    pair_rows = [r for r in proj["rows"] if r["bot"] == "Pair"]
    if not pair_rows:
        pytest.skip("pair_config not importable")
    for r in pair_rows:
        assert 2.5 <= r["window_years"] <= 2.7, (
            f"pair {r['key']} expected ~2.63yr window, got {r['window_years']}")


# ─── _v2_projection — display layer ──────────────────────────────────────

def test_v2_projection_adds_display_fields():
    p = dashboard._v2_projection()
    if not p["rows"]:
        pytest.skip("no projection rows configured")
    r = p["rows"][0]
    assert "annual_pnl_live_display" in r
    assert "pf_display" in r
    assert "window_years_display" in r
    assert "confidence_label" in r
    assert "row_class" in r


def test_v2_projection_awaiting_row_displays_em_dash():
    p = dashboard._v2_projection()
    awaiting = [r for r in p["rows"] if r.get("is_awaiting")]
    if not awaiting:
        pytest.skip("no awaiting row")
    r = awaiting[0]
    assert r["pf_display"] == "—"
    assert r["annual_pnl_live_display"] == "—"
    assert r["window_years_display"] == "—"


def test_v2_projection_low_confidence_row_gets_muted_class():
    p = dashboard._v2_projection()
    low_n_rows = [r for r in p["rows"] if r["confidence"] in ("low", "none")
                  and not r.get("is_awaiting")]
    if not low_n_rows:
        pytest.skip("no low-n rows configured")
    for r in low_n_rows:
        assert r["row_class"] == "is-low-confidence"


# ─── Template rendering ─────────────────────────────────────────────────

def test_projection_template_renders_bot_tags_and_confidence_pills():
    pytest.importorskip("jinja2")
    from dashboard_renderer import render
    ctx = dashboard._v2_test_context([])
    html = render("base.html.j2", ctx)
    if ctx["projection"]["rows"]:
        # At least one bot-tag class and one confidence-pill class
        assert 'class="bot-tag bot-tag--' in html
        assert "confidence-pill confidence-pill--" in html
