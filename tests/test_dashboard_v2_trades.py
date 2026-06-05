"""Phase D.1b — trade-log shaping + template render tests.

Run: python -m pytest tests/test_dashboard_v2_trades.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

# Importing dashboard pulls config + journal + jinja2 path. Skip if jinja2
# isn't installed — the rest of the suite still runs.
pytest.importorskip("jinja2")

import dashboard
from dashboard_renderer import render


def _trade(id, date_opened, symbol, direction, strategy, bot,
           entry_price, exit_price, qty, lev, net_pnl, result,
           exit_reason=""):
    return {
        "id": id, "date_opened": date_opened, "symbol": symbol,
        "direction": direction, "strategy": strategy, "bot": bot,
        "entry_price": entry_price, "exit_price": exit_price,
        "quantity": qty, "leverage": lev,
        "net_pnl": net_pnl, "result": result,
        "exit_reason": exit_reason,
    }


# ─── _v2_trade_rows shaping ─────────────────────────────────────────────────

def test_rows_are_newest_first():
    rows = dashboard._v2_trade_rows([
        _trade(1, "2026-05-01T00:00", "BTCUSDT", "LONG", "BTC v2",
               "Momentum", 80000, 81000, 0.01, 10, 10.0, "WIN"),
        _trade(2, "2026-05-15T08:02", "XRPUSDT", "LONG", "XRP v2",
               "Momentum", 1.47, 1.46, 169, 10, -0.54, "LOSS"),
    ])
    # Newest first
    assert rows[0]["id"] == 2
    assert rows[1]["id"] == 1


def test_row_num_descends_from_total():
    """Newest trade gets the highest #; oldest gets 1."""
    rows = dashboard._v2_trade_rows([
        _trade(1, "2026-05-01", "A", "LONG", "x", "Momentum",
               1, 2, 1, 10, 1.0, "WIN"),
        _trade(2, "2026-05-15", "B", "LONG", "x", "Momentum",
               1, 2, 1, 10, 1.0, "WIN"),
        _trade(3, "2026-05-30", "C", "LONG", "x", "Momentum",
               1, 2, 1, 10, 1.0, "WIN"),
    ])
    assert [r["row_num"] for r in rows] == [3, 2, 1]


def test_open_position_renders_em_dash_for_exit_and_pnl():
    rows = dashboard._v2_trade_rows([
        _trade(1, "2026-05-01", "BTCUSDT", "LONG", "BTC v2",
               "Momentum", 80000, None, 0.01, 10, 0.0, "OPEN"),
    ])
    assert rows[0]["exit_price"] == "—"
    assert rows[0]["net_pnl_display"] == "—"
    assert rows[0]["result_class"] == "is-open"


def test_result_class_mapping():
    rows = dashboard._v2_trade_rows([
        _trade(1, "2026-05-01", "A", "LONG", "x", "Momentum",
               1, 2, 1, 10,  1.0, "WIN"),
        _trade(2, "2026-05-02", "B", "LONG", "x", "Momentum",
               1, 0.5, 1, 10, -0.5, "LOSS"),
        _trade(3, "2026-05-03", "C", "LONG", "x", "Momentum",
               1, 1, 1, 10, 0.0, "FLAT"),
        _trade(4, "2026-05-04", "D", "LONG", "x", "Momentum",
               1, None, 1, 10, 0.0, "OPEN"),
    ])
    classes = {r["symbol"]: r["result_class"] for r in rows}
    assert classes == {"A": "is-up", "B": "is-down",
                       "C": "is-flat", "D": "is-open"}


def test_bot_class_is_lowercased_for_css_hook():
    rows = dashboard._v2_trade_rows([
        _trade(1, "2026-05-01", "X", "LONG", "y", "Whale",
               1, 2, 1, 10, 1.0, "WIN"),
        _trade(2, "2026-05-02", "Y", "LONG", "y", "Funding",
               1, 2, 1, 10, 1.0, "WIN"),
    ])
    assert {r["bot_class"] for r in rows} == {"whale", "funding"}


def test_pnl_display_sign_aware():
    rows = dashboard._v2_trade_rows([
        _trade(1, "2026-05-01", "A", "LONG", "x", "Momentum",
               1, 2,   1, 10,  10.50, "WIN"),
        _trade(2, "2026-05-02", "B", "LONG", "x", "Momentum",
               1, 0.5, 1, 10, -25.99, "LOSS"),
    ])
    by_symbol = {r["symbol"]: r["net_pnl_display"] for r in rows}
    assert by_symbol["A"] == "+$10.50"
    assert by_symbol["B"] == "−$25.99"


def test_empty_trades_returns_empty_list():
    assert dashboard._v2_trade_rows([]) == []


def test_short_date_strips_iso_time_to_minute():
    rows = dashboard._v2_trade_rows([
        _trade(1, "2026-05-15T08:02:14.328971", "A", "LONG", "x",
               "Momentum", 1, 2, 1, 10, 1.0, "WIN"),
    ])
    assert rows[0]["date_opened"] == "2026-05-15 08:02"


# ─── Template render integration ────────────────────────────────────────────

def _ctx_with_trades(trades):
    return {
        "operator": "ayott84", "env": "paper", "freshness": "0s",
        "build_sha": "abc12345", "build_ts": "2026-06-05 00:00 UTC",
        "bots": [
            {"class": "momentum", "monogram": "M", "name": "Momentum",
             "state": "live", "seen_label": "0s ago",
             "net_pnl": 0, "net_pnl_display": "$0.00",
             "trade_count": 0, "win_rate_display": "—"},
        ] * 3,  # cheat: same bot 3 times; we only test the trades tab here
        "portfolio": {"net_pnl": 0, "net_pnl_display": "$0.00",
                      "closed_count": 0, "open_count": 0,
                      "win_rate_display": "—"},
        "trades": trades,
        "whale_meta":   dashboard._v2_whale_meta([]),
        "funding_meta": dashboard._v2_funding_meta([]),
        "projection":   dashboard._v2_projection(),
    }


def test_trade_log_template_renders_sortable_thead():
    rows = dashboard._v2_trade_rows([
        _trade(1, "2026-05-15T08:02", "XRPUSDT", "LONG", "XRP v2",
               "Momentum", 1.47, 1.46, 169.8, 10, -0.54, "LOSS",
               exit_reason="BE Hit"),
    ])
    html = render("base.html.j2", _ctx_with_trades(rows))
    assert 'data-sort="net_pnl"' in html
    assert 'data-sort-type="num"' in html
    # # column is the default-active sort
    assert 'data-sort-active="desc"' in html


def test_trade_log_template_renders_data_attrs_for_each_row():
    rows = dashboard._v2_trade_rows([
        _trade(1, "2026-05-15T08:02", "XRPUSDT", "LONG", "XRP v2",
               "Momentum", 1.47, 1.46, 169.8, 10, -0.54, "LOSS",
               exit_reason="BE Hit"),
    ])
    html = render("base.html.j2", _ctx_with_trades(rows))
    # Data attrs used by the search filter
    assert 'data-symbol="xrpusdt"' in html
    assert 'data-bot="momentum"' in html
    assert 'data-result="loss"' in html
    assert 'data-direction="long"' in html
    # Visible cell values
    assert "BE Hit" in html
    assert "−$0.54" in html
    assert "exit-chip--loss" in html
    assert "result-pill--loss" in html


def test_trade_log_template_renders_open_row_with_em_dashes():
    rows = dashboard._v2_trade_rows([
        _trade(1, "2026-05-15T08:02", "BTCUSDT", "SHORT", "Whale Track BTC SHORT",
               "Whale", 80000, None, 0.01, 10, 0.0, "OPEN"),
    ])
    html = render("base.html.j2", _ctx_with_trades(rows))
    assert "result-pill--open" in html
    # The open row's PnL cell renders the em-dash
    assert "—" in html


def test_trade_log_template_search_input_is_present():
    html = render("base.html.j2", _ctx_with_trades([]))
    assert "data-trades-search" in html
    assert 'placeholder="filter by symbol' in html


def test_trade_log_template_shows_row_count_in_toolbar():
    rows = dashboard._v2_trade_rows([
        _trade(i, f"2026-05-{i:02d}", "A", "LONG", "x", "Momentum",
               1, 2, 1, 10, 1.0, "WIN") for i in range(1, 4)
    ])
    html = render("base.html.j2", _ctx_with_trades(rows))
    # Two "3" occurrences: visible count + total
    assert "data-trades-visible" in html
    assert ">3</span>" in html
    assert "of 3 rows" in html
