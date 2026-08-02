"""Module 2 Phase S4 — the stock sleeves in the dashboard.

The original framing for this whole expansion was "all modules merged
into one dashboard". This is that merge: a STOCKS sidebar group beside
the crypto BOTS group, so one page answers "what is my whole book
doing" across asset classes.

Two things the tests exist to prevent:

  * A stock sleeve rendering as a crypto bot. They have different
    benchmarks, different market hours and different pause semantics;
    quietly filing them under BOTS would make the page lie by omission.

  * The trend sleeve appearing as tradeable. It FAILED S2 and is
    refused at the daemon's gate — the dashboard must show it as a
    candidate, not as a bot that happens to be quiet.

Run: python -m pytest tests/test_dashboard_stocks_tab.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pytest.importorskip("jinja2")
pd = pytest.importorskip("pandas")

import dashboard
from dashboard_renderer import render


def _trade(bot, net, result="WIN", **k):
    return {"id": k.get("id", 1), "date_opened": "2026-07-01",
             "date_closed": k.get("date_closed", "2026-07-20"),
             "symbol": k.get("symbol", "SPY"), "direction": "LONG",
             "strategy": k.get("strategy", "SPY 1D StockRev"), "bot": bot,
             "entry_price": 500.0, "exit_price": 505.0, "quantity": 4,
             "leverage": 1, "net_pnl": net, "result": result,
             "exit_reason": k.get("exit_reason", "Above SMA")}


# ─── Registration surfaces ────────────────────────────────────────────────

def test_stock_classes_have_labels():
    for cls, label in (("stockrev", "Reversion"), ("stockdual", "Dual momentum"),
                        ("stocktrend", "Trend")):
        assert dashboard._BOT_CLASS_TO_LABEL.get(cls) == label


def test_stock_pause_flag_registered():
    assert dashboard._PAUSE_FLAGS.get("stockrev") == ("stock_config",
                                                        "STOCK_PAUSED")


def test_kill_switch_panel_now_shows_stock_owners():
    """S0 deliberately hid them until the module existed. It does now."""
    shown = {o["owner"] for o in dashboard._v2_kill_switch_panel()["owners"]}
    assert {"stockrev", "stockdual"} <= shown


# ─── Meta builder ─────────────────────────────────────────────────────────

def test_stock_meta_counts_only_stock_trades():
    trades = [
        _trade("StockRev", 5.0),
        _trade("StockRev", -2.0, result="LOSS"),
        _trade("StockDual", 8.0, strategy="GEM 1M StockDual"),
        _trade("Scalp", 100.0, symbol="BTCUSDT"),          # ignored
    ]
    meta = dashboard._v2_stock_meta(trades)
    assert meta["closed_count"] == 3
    assert meta["net_pnl_display"].startswith("$")
    assert "sleeves" in meta


def test_stock_meta_separates_approved_from_candidates():
    """The trend sleeve failed S2. It must not read as tradeable."""
    meta = dashboard._v2_stock_meta([])
    approved = {s["sleeve"] for s in meta["sleeves"] if s["approved"]}
    assert approved == {"rev", "dual"}
    trend = next(s for s in meta["sleeves"] if s["sleeve"] == "trend")
    assert trend["approved"] is False
    assert "FAIL" in trend["verdict"].upper()


def test_stock_meta_carries_the_buy_hold_caveat():
    """The single most misreadable fact about these sleeves: they lose
    badly to simply owning the index on absolute return."""
    meta = dashboard._v2_stock_meta([])
    row = next(s for s in meta["sleeves"] if s["sleeve"] == "rev")
    assert row.get("benchmark_note")
    assert "buy-hold" in row["benchmark_note"].lower()


def test_stock_meta_survives_missing_config(monkeypatch):
    """Watchdog philosophy: a dashboard build never dies on one panel."""
    import builtins
    real = builtins.__import__

    def boom(name, *a, **kw):
        if name == "stock_config":
            raise ImportError("simulated")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", boom)
    meta = dashboard._v2_stock_meta([])
    assert meta["closed_count"] == 0


# ─── Template render ──────────────────────────────────────────────────────

def test_stocks_tab_renders_in_the_full_page():
    ctx = dashboard._v2_test_context()
    assert "stock_meta" in ctx
    html = render("base.html.j2", ctx)
    assert 'id="tab-stocks"' in html
    assert "STOCKS" in html


def test_sidebar_keeps_modules_separate():
    """A stock sleeve filed under the crypto BOTS group would make the
    page lie by omission — different hours, benchmark and semantics."""
    html = render("base.html.j2", dashboard._v2_test_context())
    stocks_i = html.find("STOCKS")
    bots_i = html.find(">BOTS<")
    assert stocks_i > 0 and bots_i > 0
    assert stocks_i != bots_i


def test_stocks_tab_shows_the_paper_and_calendar_state():
    html = render("base.html.j2", dashboard._v2_test_context())
    seg = html[html.find('id="tab-stocks"'):]
    seg = seg[:seg.find("</section>")]
    assert "paper" in seg.lower()
