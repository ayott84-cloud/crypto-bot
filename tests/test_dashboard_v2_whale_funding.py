"""Phase D.1c — Whale + Funding tab content tests.

Run: python -m pytest tests/test_dashboard_v2_whale_funding.py -v
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


def _trade(bot, exit_price, net_pnl, result, **k):
    base = {
        "id": k.get("id", 1),
        "date_opened": k.get("date_opened", "2026-05-01"),
        "symbol": k.get("symbol", "BTCUSDT"),
        "direction": k.get("direction", "LONG"),
        "strategy": k.get("strategy", ""),
        "bot": bot,
        "entry_price": 100, "exit_price": exit_price,
        "quantity": 1, "leverage": 10,
        "net_pnl": net_pnl, "result": result,
        "exit_reason": k.get("exit_reason", ""),
    }
    return base


# ─── _v2_whale_meta ─────────────────────────────────────────────────────────

def test_whale_meta_paused_when_whale_paused_true(monkeypatch):
    """The actual whale_config has WHALE_PAUSED=true; mirror that here."""
    meta = dashboard._v2_whale_meta([])
    # Default deployment has WHALE_PAUSED=true; the function should
    # report it accurately. If a future repo state flips this default,
    # update the assertion.
    assert meta["paused"] is True
    assert "consensus" in meta["pause_reason"].lower()


def test_whale_meta_counts_only_whale_trades():
    """Momentum + funding trades must not affect whale stats."""
    trades = [
        _trade("Whale",    150, 50.0,  "WIN"),
        _trade("Whale",     80, -20.0, "LOSS"),
        _trade("Momentum", 200, 100.0, "WIN"),     # ignored
        _trade("Funding",  120,  10.0, "WIN"),     # ignored
    ]
    meta = dashboard._v2_whale_meta(trades)
    assert meta["closed_count"] == 2
    assert meta["wins"] == 1
    assert meta["losses"] == 1
    assert meta["win_rate_display"] == "50.0%"


def test_whale_meta_empty_handles_no_trades_gracefully():
    meta = dashboard._v2_whale_meta([])
    assert meta["closed_count"] == 0
    assert meta["win_rate_display"] == "—"
    assert meta["net_pnl_display"] == "$0.00"
    assert meta["best_display"] == "$0.00"
    assert meta["worst_display"] == "$0.00"


def test_whale_meta_best_and_worst_track_extremes():
    trades = [
        _trade("Whale", 150,  50.0,  "WIN"),
        _trade("Whale", 160, 100.0,  "WIN"),
        _trade("Whale",  80, -20.0,  "LOSS"),
        _trade("Whale",  70, -30.0,  "LOSS"),
    ]
    meta = dashboard._v2_whale_meta(trades)
    assert meta["best_display"]  == "+$100.00"
    assert meta["worst_display"] == "−$30.00"


# ─── _v2_funding_meta ───────────────────────────────────────────────────────

def test_funding_meta_exposes_universe_mode_and_threshold():
    meta = dashboard._v2_funding_meta([])
    assert meta["universe_mode"] in ("OI", "TOP100")
    assert meta["percentile"] > 0
    assert meta["absolute_floor_pct"] > 0
    assert meta["window_minutes"] > 0
    assert isinstance(meta["fixing_hours"], list)


def test_funding_meta_direction_toggles_default_true():
    meta = dashboard._v2_funding_meta([])
    assert meta["allow_long_fade"]  is True
    assert meta["allow_short_fade"] is True


def test_funding_meta_notional_is_margin_times_leverage():
    meta = dashboard._v2_funding_meta([])
    assert meta["notional_usd"] == meta["margin_usd"] * meta["leverage"]


def test_funding_meta_counts_only_funding_trades():
    trades = [
        _trade("Funding",  120, 10.0, "WIN"),
        _trade("Funding",   90, -5.0, "LOSS"),
        _trade("Momentum", 200, 50.0, "WIN"),     # ignored
        _trade("Whale",     80, -10.0, "LOSS"),   # ignored
    ]
    meta = dashboard._v2_funding_meta(trades)
    assert meta["closed_count"] == 2


# ─── Template render — whale tab ────────────────────────────────────────────

def _ctx(trades=None, **overrides):
    trades = trades or []
    ctx = {
        "operator": "ayott84", "env": "paper", "freshness": "0s",
        "build_sha": "abc12345", "build_ts": "2026-06-05 00:00 UTC",
        "bots": [{"class": "momentum", "monogram": "M", "name": "Momentum",
                  "state": "live", "seen_label": "0s ago",
                  "net_pnl": 0, "net_pnl_display": "$0.00",
                  "trade_count": 0, "win_rate_display": "—"}] * 3,
        "portfolio": {"net_pnl": 0, "net_pnl_display": "$0.00",
                      "closed_count": 0, "open_count": 0,
                      "win_rate_display": "—"},
        "trades": trades,
        "momentum_meta": dashboard._v2_momentum_meta(trades if "trades" in dir() else []),
        "whale_meta":   dashboard._v2_whale_meta(trades),
        "funding_meta": dashboard._v2_funding_meta(trades),
        "breakout_meta": dashboard._v2_breakout_meta(trades),
        "pair_meta":     dashboard._v2_pair_meta(trades),
        "reversal_meta":  dashboard._v2_reversal_meta(trades),
        "scalp_meta":     dashboard._v2_scalp_meta(trades),
        "crossover_meta": dashboard._v2_crossover_meta(trades),
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
    ctx.update(overrides)
    return ctx


def test_whale_tab_renders_dormant_banner_when_paused():
    html = render("base.html.j2", _ctx())
    assert "banner--dormant" in html
    assert "DORMANT" in html
    assert "PAUSED" in html


def test_whale_tab_renders_kv_grid_stats():
    trades = [
        _trade("Whale", 150,  50.0, "WIN"),
        _trade("Whale",  80, -20.0, "LOSS"),
    ]
    html = render("base.html.j2", _ctx(trades=trades))
    assert "NET PnL" in html
    assert "WIN RATE" in html
    assert "BEST TRADE" in html
    assert "WORST TRADE" in html
    assert "AVG LOSS" in html


# ─── Template render — funding tab ──────────────────────────────────────────

def test_funding_tab_renders_universe_mode_banner():
    html = render("base.html.j2", _ctx())
    assert "Universe mode" in html
    # OI is the default; either OI or TOP100 is fine
    assert ("OI" in html or "TOP100" in html)


def _pin_pause_flags(monkeypatch):
    """Make the banner tests host-independent. Every non-funding bot's
    AWAITING SIGNAL banner renders when its *_PAUSED env flag is false
    and it has zero trades in the fixture ctx — true on the droplet's
    .env, false on a dev box. Pin: others PAUSED, funding LIVE, so the
    assertions exercise ONLY the funding tab's banner logic. (This test
    failed on the droplet on Jul 16 2026 for exactly this env leak.)"""
    import breakout_config, crossover_config, pair_config
    import reversal_config, scalp_config, funding_config
    for mod, flag in ((breakout_config, "BREAKOUT_PAUSED"),
                       (crossover_config, "CROSSOVER_PAUSED"),
                       (pair_config, "PAIR_PAUSED"),
                       (reversal_config, "REVERSAL_PAUSED"),
                       (scalp_config, "SCALP_PAUSED")):
        monkeypatch.setattr(mod, flag, True)
    monkeypatch.setattr(funding_config, "FUNDING_PAUSED", False)


def test_funding_tab_renders_awaiting_signal_banner_when_no_trades(monkeypatch):
    _pin_pause_flags(monkeypatch)
    html = render("base.html.j2", _ctx(trades=[]))
    assert "AWAITING SIGNAL" in html


def test_funding_tab_omits_awaiting_banner_when_trades_exist(monkeypatch):
    _pin_pause_flags(monkeypatch)
    html = render("base.html.j2", _ctx(trades=[
        _trade("Funding", 120, 10.0, "WIN"),
    ]))
    assert "AWAITING SIGNAL" not in html


def test_funding_tab_renders_all_config_rows():
    html = render("base.html.j2", _ctx())
    for label in (
        "Universe mode", "Percentile threshold", "Absolute floor",
        "Min OI (per signal)", "Execution window",
        "Vol regime gate", "Direction toggles", "Sizing",
    ):
        assert label in html, f"Config row {label!r} missing"


def test_funding_tab_renders_direction_toggle_tags():
    html = render("base.html.j2", _ctx())
    # Both toggles are ON by default → both visible at full opacity
    assert "LONG ON" in html
    assert "SHORT ON" in html
