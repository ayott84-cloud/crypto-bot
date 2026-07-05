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
    """Each breakout row uses its OWN window, not the global BACKTEST_YEARS
    (5.3). P4 Step-2 refresh: honest long-window stats are 1.9yr (1h) /
    5.0-7.8yr (4h, listing-capped) — legitimate values, so the guard is
    now 'never exactly the 5.3 global', not a blanket ceiling."""
    from config import BACKTEST_YEARS
    proj = dashboard._compute_yearly_projection()
    breakout_rows = [r for r in proj["rows"] if r["bot"] == "Breakout"]
    if not breakout_rows:
        pytest.skip("breakout_config not importable")
    for r in breakout_rows:
        assert 0 < r["window_years"] <= 8.0, (
            f"breakout {r['key']} window {r['window_years']} out of range")
        assert r["window_years"] != BACKTEST_YEARS, (
            f"breakout {r['key']} window equals the global BACKTEST_YEARS "
            "divisor — per-row years field is being ignored")


def test_projection_pair_uses_own_window():
    """Each pair row has its own backtest window (post-multi-pair refactor:
    ETHBTC = 2.63yr, BTCLTC = 2.74yr). Both should fall in the same
    "~daily-bar-1000-row" ballpark."""
    proj = dashboard._compute_yearly_projection()
    pair_rows = [r for r in proj["rows"] if r["bot"] == "Pair"]
    if not pair_rows:
        pytest.skip("pair_config not importable")
    for r in pair_rows:
        assert 2.5 <= r["window_years"] <= 2.8, (
            f"pair {r['key']} expected 2.5-2.8yr window, got {r['window_years']}")


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


# ─── Phase L.1 — projection honesty pass ─────────────────────────────────

# L.1a — PF display cap

def test_pf_display_caps_999_sentinel_as_infinity():
    text, tooltip = dashboard._pf_display(999.0)
    assert text == "∞"
    assert "∞" in tooltip


def test_pf_display_caps_above_10_with_gt_symbol():
    text, tooltip = dashboard._pf_display(100.34)
    assert text == ">10"
    assert "100.34" in tooltip


def test_pf_display_passes_through_normal_values():
    text, tooltip = dashboard._pf_display(2.81)
    assert text == "2.81"
    assert tooltip == ""


def test_pf_display_handles_zero_and_none():
    assert dashboard._pf_display(0)[0] == "0.00"
    assert dashboard._pf_display(None)[0] == "—"


# L.1b — years field added ONLY to Phase K _MOMENTUM_PROMOTIONS

def test_phase_k_momentum_promotions_have_years_field():
    """Every entry in _MOMENTUM_PROMOTIONS must carry a `years` field.
    Without it, the projection falls back to BACKTEST_YEARS=5.3 and
    under-annualizes 4H rows by ~11×."""
    from config import _MOMENTUM_PROMOTIONS
    for name, _sym, _interval, stats in _MOMENTUM_PROMOTIONS:
        assert "years" in stats, f"{name} stats dict missing years"
        assert stats["years"] > 0, f"{name} has years <= 0"


def test_4h_promotions_use_046_window():
    from config import _MOMENTUM_PROMOTIONS
    for name, _sym, interval, stats in _MOMENTUM_PROMOTIONS:
        if interval == "4h":
            assert stats["years"] == 0.46, (
                f"{name} 4H window should be 0.46yr, got {stats['years']}")


def test_1d_promotions_use_274_window():
    from config import _MOMENTUM_PROMOTIONS
    for name, _sym, interval, stats in _MOMENTUM_PROMOTIONS:
        if interval == "1d":
            assert stats["years"] == 2.74, (
                f"{name} 1D window should be 2.74yr, got {stats['years']}")


def test_momentum_stats_are_internally_consistent():
    """Superseded contract (Jul 5 2026): the old guard kept `years` OFF
    legacy rows because their pnl_pct came from 5.3yr TV windows and
    relied on the BACKTEST_YEARS fallback. After the Step-2 cut, every
    remaining ASSETS row's stats were REPLACED with honest-replay
    numbers where pnl_pct and years come from the SAME window — so the
    new invariant is the opposite: every row carries its own years and
    cites the honest source (no fallback reliance anywhere)."""
    from config import ASSETS
    for key, cfg in ASSETS.items():
        stats = cfg.get("backtest_stats")
        assert stats is not None, f"{key} missing backtest_stats"
        assert "years" in stats, f"{key} missing same-window years"
        assert "honest replay" in stats.get("source", ""), (
            f"{key} cites a non-honest source: {stats.get('source')!r}")


# L.1c — sqrt(n/20) sample-size discount

def test_sample_size_discount_full_at_n_geq_20():
    assert dashboard._sample_size_discount(20)  == 1.0
    assert dashboard._sample_size_discount(50)  == 1.0
    assert dashboard._sample_size_discount(100) == 1.0


def test_sample_size_discount_sqrt_below_20():
    import math
    assert dashboard._sample_size_discount(5) == pytest.approx(math.sqrt(0.25), rel=0.01)
    assert dashboard._sample_size_discount(10) == pytest.approx(math.sqrt(0.5), rel=0.01)
    assert dashboard._sample_size_discount(15) == pytest.approx(math.sqrt(0.75), rel=0.01)


def test_sample_size_discount_zero_when_no_trades():
    assert dashboard._sample_size_discount(0) == 0.0
    assert dashboard._sample_size_discount(-1) == 0.0


def test_projection_row_carries_sample_chip_for_low_n():
    p = dashboard._v2_projection()
    low_n_rows = [r for r in p["rows"]
                   if 0 < r.get("trades", 0) < 20 and not r.get("is_awaiting")]
    if not low_n_rows:
        pytest.skip("no low-n rows configured")
    for r in low_n_rows:
        assert r["sample_chip"], (
            f"row {r['key']} has trades={r['trades']} but no sample_chip")
        assert f"n={r['trades']}" in r["sample_chip"]


# L.1d — Headline split (Directional + Pair-spread)

def test_projection_splits_directional_and_pair():
    p = dashboard._v2_projection()
    assert "directional_annual" in p
    assert "pair_annual" in p
    assert "directional_annual_display" in p
    assert "pair_annual_display" in p


def test_projection_directional_excludes_pair_rows():
    p = dashboard._v2_projection()
    pair_total = sum(r["annual_pnl_live"] for r in p["rows"]
                      if r.get("bot") == "Pair" and not r.get("is_awaiting"))
    assert abs(p["pair_annual"] - pair_total) < 0.01
    directional_total = sum(r["annual_pnl_live"] for r in p["rows"]
                             if r.get("bot") != "Pair" and not r.get("is_awaiting"))
    assert abs(p["directional_annual"] - directional_total) < 0.01


# L.1e — Observed-vs-projected reality check

def test_observed_annual_pnl_excludes_open_positions():
    from datetime import datetime, timezone, timedelta
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    trades = [
        {"date_closed": recent, "net_pnl": 10.0, "exit_reason": "TP1"},
        {"date_closed": None,   "net_pnl": 0.0,  "exit_reason": ""},
    ]
    annual, n = dashboard._v2_observed_annual_pnl(trades, window_days=30)
    assert n == 1
    assert annual > 0


def test_observed_annual_pnl_excludes_reconciler_zeros():
    from datetime import datetime, timezone, timedelta
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    trades = [
        {"date_closed": recent, "net_pnl": 10.0, "exit_reason": "TP1"},
        {"date_closed": recent, "net_pnl":  0.0,
            "exit_reason": "reconciled by tools/reconcile_journal.py"},
    ]
    annual, n = dashboard._v2_observed_annual_pnl(trades, window_days=30)
    assert n == 1, "reconciler rows must not be counted as real activity"


def test_observed_annual_pnl_annualizes_correctly():
    from datetime import datetime, timezone, timedelta
    recent = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    trades = [{"date_closed": recent, "net_pnl": 100.0, "exit_reason": "TP1"}]
    annual, _ = dashboard._v2_observed_annual_pnl(trades, window_days=30)
    # $100 over 30 days × (365/30) = $1216.67
    assert 1200 < annual < 1230


def test_observed_annual_pnl_outside_window_excluded():
    from datetime import datetime, timezone, timedelta
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    trades = [{"date_closed": old, "net_pnl": 100.0, "exit_reason": "TP1"}]
    annual, n = dashboard._v2_observed_annual_pnl(trades, window_days=30)
    assert annual == 0
    assert n == 0


def test_projection_surfaces_observed_when_trades_provided():
    from datetime import datetime, timezone, timedelta
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    trades = [{"date_closed": recent, "net_pnl": 50.0, "exit_reason": "TP1"}]
    p = dashboard._v2_projection(trades=trades)
    assert p["observed_n"] == 1
    assert p["observed_annual"] > 0
    assert p["observed_annual_display"]
