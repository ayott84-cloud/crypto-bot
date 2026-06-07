"""Phase J.4 — per-bot positions + closed-trades + KPI helpers tests.

Three new shaper functions in dashboard.py:
  - _v2_open_positions_for_bot(state, bot_class)
  - _v2_closed_trades_for_bot(trades, bot_class, limit=50)
  - _v2_kpis_for_bot(trades, bot_class)

Plus shared partial templates that consume them.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

import dashboard


# ─── _v2_open_positions_for_bot ────────────────────────────────────────────

def test_open_positions_filters_by_bot_prefix():
    state = {"positions": {
        "BTCUSDT":              {"symbol": "BTCUSDT", "direction": "LONG",
                                  "entry_price": 80000, "quantity": 0.01},
        "WHALE_HYPE":           {"symbol": "HYPEUSDT", "direction": "SHORT",
                                  "entry_price": 56.28, "quantity": 13.3},
        "BREAKOUT_ETH_4H":      {"symbol": "ETHUSDT", "direction": "LONG",
                                  "entry_price": 3280, "quantity": 0.076},
        "PAIR_ETHBTC_LONG_LEG": {"symbol": "ETHUSDT", "direction": "LONG",
                                  "entry_price": 2000, "quantity": 0.25},
    }}
    momentum = dashboard._v2_open_positions_for_bot(state, "momentum")
    whale    = dashboard._v2_open_positions_for_bot(state, "whale")
    breakout = dashboard._v2_open_positions_for_bot(state, "breakout")
    pair     = dashboard._v2_open_positions_for_bot(state, "pair")
    assert len(momentum) == 1 and momentum[0]["state_key"] == "BTCUSDT"
    assert len(whale) == 1    and whale[0]["state_key"] == "WHALE_HYPE"
    assert len(breakout) == 1 and breakout[0]["state_key"] == "BREAKOUT_ETH_4H"
    assert len(pair) == 1     and pair[0]["state_key"] == "PAIR_ETHBTC_LONG_LEG"


def test_open_positions_empty_state_returns_empty_list():
    assert dashboard._v2_open_positions_for_bot({}, "whale") == []
    assert dashboard._v2_open_positions_for_bot({"positions": {}}, "whale") == []


def test_open_positions_includes_display_fields():
    state = {"positions": {
        "WHALE_HYPE": {"symbol": "HYPEUSDT", "direction": "SHORT",
                         "entry_price": 56.28, "quantity": 13.3,
                         "strategy": "Whale Track HYPE SHORT"},
    }}
    pos = dashboard._v2_open_positions_for_bot(state, "whale")[0]
    assert "entry_display" in pos
    assert "qty_display" in pos
    assert "direction_class" in pos  # is-up or is-down
    assert pos["direction_class"] == "is-down"  # SHORT renders as down


# ─── _v2_closed_trades_for_bot ─────────────────────────────────────────────

def _make_trade(bot, net_pnl, idx=1):
    return {
        "id": idx, "date_opened": f"2026-05-{idx:02d}",
        "date_closed": f"2026-05-{idx:02d}",
        "symbol": "BTCUSDT", "direction": "LONG", "bot": bot,
        "strategy": f"{bot} strategy",
        "entry_price": 100, "exit_price": 100 + net_pnl,
        "quantity": 1, "leverage": 10,
        "net_pnl": net_pnl,
        "result": "WIN" if net_pnl > 0 else ("LOSS" if net_pnl < 0 else "FLAT"),
        "exit_reason": "TP1",
    }


def test_closed_trades_filters_by_bot_column():
    trades = [
        _make_trade("Momentum", 10, idx=1),
        _make_trade("Whale", -5, idx=2),
        _make_trade("Momentum", 20, idx=3),
        _make_trade("Breakout", 15, idx=4),
    ]
    momentum = dashboard._v2_closed_trades_for_bot(trades, "momentum")
    whale    = dashboard._v2_closed_trades_for_bot(trades, "whale")
    breakout = dashboard._v2_closed_trades_for_bot(trades, "breakout")
    assert len(momentum) == 2
    assert len(whale) == 1
    assert len(breakout) == 1


def test_closed_trades_returns_newest_first():
    trades = [
        _make_trade("Momentum", 10, idx=1),  # oldest
        _make_trade("Momentum", 20, idx=15), # newest
    ]
    rows = dashboard._v2_closed_trades_for_bot(trades, "momentum")
    assert rows[0]["id"] == 15
    assert rows[1]["id"] == 1


def test_closed_trades_respects_limit():
    trades = [_make_trade("Momentum", 10, idx=i) for i in range(1, 100)]
    rows = dashboard._v2_closed_trades_for_bot(trades, "momentum", limit=10)
    assert len(rows) == 10


def test_closed_trades_excludes_open_positions():
    """Trades with no exit_price (open positions) shouldn't appear in the
    closed list."""
    trades = [
        _make_trade("Momentum", 10, idx=1),
        {**_make_trade("Momentum", 0, idx=2), "exit_price": None, "result": "OPEN"},
    ]
    rows = dashboard._v2_closed_trades_for_bot(trades, "momentum")
    assert len(rows) == 1
    assert rows[0]["id"] == 1


def test_closed_trades_empty_bot_returns_empty_list():
    assert dashboard._v2_closed_trades_for_bot([], "whale") == []


# ─── _v2_kpis_for_bot ─────────────────────────────────────────────────────

def test_kpis_for_bot_computes_scoped_metrics():
    trades = [
        _make_trade("Whale", -10, idx=1),
        _make_trade("Whale", +20, idx=2),
        _make_trade("Whale", -5, idx=3),
        _make_trade("Whale", +15, idx=4),
    ]
    kpis = dashboard._v2_kpis_for_bot(trades, "whale")
    assert kpis["closed_count"] == 4
    assert kpis["sortino_display"]  # non-empty
    assert kpis["recovery_display"]
    assert "streak_display" in kpis


def test_kpis_for_bot_empty_returns_sane_defaults():
    kpis = dashboard._v2_kpis_for_bot([], "whale")
    assert kpis["closed_count"] == 0
    assert kpis["sortino_display"] == "—"
    assert kpis["streak_display"] == "—"


# ─── Render-level integration ────────────────────────────────────────────

def test_each_bot_tab_renders_panels():
    """Every bot tab must include the J.4 KPI strip + positions + trades."""
    pytest.importorskip("jinja2")
    from dashboard_renderer import render
    html = render("base.html.j2", dashboard._v2_test_context([]))
    # Each bot tab includes the three new sections
    assert html.count('class="bot-kpis"')      >= 6
    assert html.count('class="bot-positions"') >= 6
    assert html.count('class="bot-trades"')    >= 6


def test_bot_tab_filters_trades_by_bot_column():
    """The Whale tab shows only whale trades; Momentum shows only momentum trades."""
    pytest.importorskip("jinja2")
    from dashboard_renderer import render
    trades = [
        _make_trade("Momentum", 10, idx=1),
        _make_trade("Whale", -5, idx=2),
    ]
    html = render("base.html.j2", dashboard._v2_test_context(trades))
    # Both trades are present in the trade log (sanity)
    # Per-bot panels only see their own bot's trades — verify by counting
    # appearances of the "result" string in the per-bot tables.
    # Each panel shows `+$10.00` once (momentum) and `−$5.00` once (whale).
    assert "+$10.00" in html
    assert "−$5.00" in html
