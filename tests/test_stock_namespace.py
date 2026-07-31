"""Module 2 Phase S0 — stock namespace seams.

Two owner axes, deliberately distinct (document or be confused later):

  * position_manager owner = "stock" — ONE daemon runs all three daily
    sleeves, so they share one state namespace and one save_state owner.
  * journal / kill_switch / revalidation owners = PER SLEEVE
    (stocktrend, stockdual, stockrev) — each sleeve needs its own loss
    streak, its own gate step, and its own fleet_review row, exactly as
    every crypto bot does.

The classifier is suffix-based (journal._bot_tag / kill_switch._bot_of),
so strategy names must END with the tag word. The default sink is
"Momentum"/"momentum": an unclassified stock strategy would silently
pollute crypto momentum stats — the same defect class that produced the
215-trade pair-thrash mis-attribution in July.

Run: python -m pytest tests/test_stock_namespace.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest


# ─── position_manager: one namespace for the stock daemon ─────────────────

def test_stock_prefix_classifies_to_stock():
    from position_manager import _bot_of_key
    assert _bot_of_key("STOCK_TREND_SPY") == "stock"
    assert _bot_of_key("STOCK_DUAL_VEU") == "stock"
    assert _bot_of_key("STOCK_REV_QQQ") == "stock"


def test_stock_prefix_does_not_capture_crypto_keys():
    from position_manager import _bot_of_key
    assert _bot_of_key("SCALP_BTC_5M") == "scalp"
    assert _bot_of_key("BREAKOUT_ETH_4H") == "breakout"
    assert _bot_of_key("BTC_1D") == "momentum"          # default sink intact


def test_stock_has_a_toplevel_key_set():
    from position_manager import _TOPLEVEL_BY_BOT
    assert "stock" in _TOPLEVEL_BY_BOT
    assert "stock_cooldowns" in _TOPLEVEL_BY_BOT["stock"]


def test_stock_state_survives_a_crypto_bot_save(tmp_path, monkeypatch):
    """The merge invariant that matters: a crypto bot saving its own
    state must not drop the stock daemon's positions or top-level keys."""
    import position_manager as pm
    monkeypatch.setattr(pm, "STATE_FILE", tmp_path / "state.json")

    stock_state = {
        "positions": {"STOCK_TREND_SPY": {"symbol": "SPY", "entry_price": 500.0}},
        "stock_cooldowns": {"SPY": "2026-08-03T15:00:00+00:00"},
    }
    pm.save_state(stock_state, owner="stock")

    scalp_state = pm.load_state()
    scalp_state.setdefault("positions", {})["SCALP_BTC_5M"] = {"symbol": "BTCUSDT"}
    pm.save_state(scalp_state, owner="scalp")

    merged = pm.load_state()
    assert "STOCK_TREND_SPY" in merged["positions"], "stock position dropped"
    assert "SCALP_BTC_5M" in merged["positions"]
    assert merged.get("stock_cooldowns", {}).get("SPY"), "stock top-level dropped"


# ─── journal._bot_tag: per-sleeve tags ────────────────────────────────────

@pytest.mark.parametrize("strategy,expected", [
    ("SPY 1M StockTrend",   "StockTrend"),
    ("EFA 1M StockTrend",   "StockTrend"),
    ("GEM 1M StockDual",    "StockDual"),
    ("QQQ 1D StockRev",     "StockRev"),
    ("StockTrend",          "StockTrend"),      # bare tag fallback
    ("StockDual",           "StockDual"),
    ("StockRev",            "StockRev"),
])
def test_bot_tag_classifies_stock_sleeves(strategy, expected):
    import journal
    assert journal._bot_tag(strategy) == expected


def test_bot_tag_crypto_unaffected():
    import journal
    assert journal._bot_tag("BTC 5m Scalp") == "Scalp"
    assert journal._bot_tag("ETH 4H Breakout") == "Breakout"
    assert journal._bot_tag("BTC 1D Momentum") == "Momentum"
    assert journal._bot_tag("Whale Track BTC") == "Whale"


def test_no_stock_strategy_falls_into_the_momentum_sink():
    """Regression guard for the July mis-attribution class."""
    import journal
    for s in ("SPY 1M StockTrend", "GEM 1M StockDual", "QQQ 1D StockRev"):
        assert journal._bot_tag(s) != "Momentum", s


# ─── kill_switch: per-sleeve owners ───────────────────────────────────────

@pytest.mark.parametrize("strategy,owner", [
    ("SPY 1M StockTrend", "stocktrend"),
    ("GEM 1M StockDual",  "stockdual"),
    ("QQQ 1D StockRev",   "stockrev"),
])
def test_bot_of_classifies_stock_sleeves(strategy, owner):
    import kill_switch
    assert kill_switch._bot_of(strategy) == owner


def test_stock_owners_are_recognized():
    """An unrecognized owner makes _filter_to_owner return EVERY trade,
    so a stock sleeve's loss streak would be computed against the whole
    fleet."""
    import kill_switch
    for o in ("stocktrend", "stockdual", "stockrev"):
        assert o in kill_switch._RECOGNIZED_OWNERS, o


def test_filter_to_owner_isolates_a_stock_sleeve():
    import kill_switch
    trades = [
        {"strategy": "SPY 1M StockTrend", "net_pnl": -1.0},
        {"strategy": "GEM 1M StockDual",  "net_pnl": -2.0},
        {"strategy": "BTC 5m Scalp",      "net_pnl": -3.0},
    ]
    only = kill_switch._filter_to_owner(trades, "stocktrend")
    assert len(only) == 1
    assert only[0]["strategy"] == "SPY 1M StockTrend"


def test_status_summary_reports_stock_owners():
    import kill_switch
    summary = kill_switch.status_summary()
    for o in ("stocktrend", "stockdual", "stockrev"):
        assert o in summary, f"{o} missing from status_summary"
