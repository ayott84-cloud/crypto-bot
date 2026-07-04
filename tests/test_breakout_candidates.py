"""Phase K — Breakout candidate-asset staging tests.

BREAKOUT_CANDIDATE_ASSETS is a separate dict from BREAKOUT_ASSETS so the
new candidates don't auto-trade. breakout_main never iterates the
candidate dict — only tools/validate_breakout_candidates.py does.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

import dashboard


def test_candidates_module_importable():
    from breakout_config import BREAKOUT_CANDIDATE_ASSETS
    assert isinstance(BREAKOUT_CANDIDATE_ASSETS, dict)
    assert len(BREAKOUT_CANDIDATE_ASSETS) >= 1


def test_candidates_have_required_shape():
    from breakout_config import BREAKOUT_CANDIDATE_ASSETS
    required = {"symbol", "interval", "donchian_period",
                  "donchian_exit_period", "sl_atr_mult",
                  "strategy_name"}
    for name, cfg in BREAKOUT_CANDIDATE_ASSETS.items():
        missing = required - set(cfg.keys())
        assert not missing, f"candidate {name} missing {missing}"


def test_candidates_do_not_overlap_with_live_assets():
    """Promotion = move from CANDIDATE to ASSETS. They must not coexist
    or breakout_main would double-iterate the same key."""
    from breakout_config import BREAKOUT_CANDIDATE_ASSETS, BREAKOUT_ASSETS
    overlap = set(BREAKOUT_CANDIDATE_ASSETS) & set(BREAKOUT_ASSETS)
    assert not overlap, f"keys in both dicts: {overlap}"


def test_candidates_not_iterated_by_breakout_main():
    """The ENTRY loop must never iterate CANDIDATE_ASSETS — that would
    silently activate untested strategies. The one legitimate reference
    is _cfg_for_open_position's EXIT-management fallback (P4 Step-2
    orphan guard: a demoted asset's open position keeps being managed
    until it closes, but can never open new trades)."""
    bm_path = BOT_DIR / "breakout_main.py"
    if not bm_path.exists():
        pytest.skip("breakout_main.py not present")
    text = bm_path.read_text(encoding="utf-8")
    # Entry iteration stays on the live dict only
    assert "for asset_name, cfg in BREAKOUT_ASSETS.items()" in text
    assert "BREAKOUT_CANDIDATE_ASSETS.items()" not in text
    # Every candidate-dict reference lives inside the exit-management
    # helper, nowhere else
    import inspect
    import breakout_main
    helper_src = inspect.getsource(breakout_main._cfg_for_open_position)
    outside = text.replace(helper_src, "")
    assert "BREAKOUT_CANDIDATE_ASSETS" not in outside


def test_breakout_meta_exposes_candidate_rows():
    """Dashboard surfaces candidates so the operator sees what's staged."""
    meta = dashboard._v2_breakout_meta([])
    assert "candidate_assets" in meta
    # When candidates are configured, the list is non-empty
    from breakout_config import BREAKOUT_CANDIDATE_ASSETS
    assert len(meta["candidate_assets"]) == len(BREAKOUT_CANDIDATE_ASSETS)


def test_breakout_template_renders_candidate_section():
    pytest.importorskip("jinja2")
    from dashboard_renderer import render
    ctx = dashboard._v2_test_context([])
    html = render("base.html.j2", ctx)
    if ctx["breakout_meta"]["candidate_assets"]:
        assert "Candidate assets" in html
        assert "awaiting backtest" in html
