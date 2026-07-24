"""tools/fleet_review.py — the one-command periodic fleet review.

Pure-function tests; no journal/network access.

Run: python -m pytest tests/test_fleet_review.py -v
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


def _t(bot, pnl, days_ago=2, symbol="ETHUSDT", reason="TP Hit"):
    closed = (datetime.now() - timedelta(days=days_ago)).isoformat()
    return {"bot": bot, "result": "WIN" if pnl > 0 else "LOSS",
            "net_pnl": pnl, "symbol": symbol, "exit_reason": reason,
            "date_closed": closed, "exit_price": 100.0}


def test_bot_stats_aggregation():
    from tools.fleet_review import bot_stats
    trades = [_t("Scalp", 3.0), _t("Scalp", -1.0), _t("Scalp", 2.0),
               _t("Momentum", -4.0), _t("Scalp", 5.0, days_ago=20)]  # old
    stats = bot_stats(trades, days=14)
    s = stats["Scalp"]
    assert s["n"] == 3
    assert s["wins"] == 2
    assert s["net"] == pytest.approx(4.0)
    assert s["pf"] == pytest.approx(5.0)          # 5 gross win / 1 gross loss
    assert stats["Momentum"]["pf"] == 0.0


def test_step4_gate_verdict():
    from tools.fleet_review import step4_verdict
    assert step4_verdict(pf=1.54, n=12)["verdict"] == "PASS"
    assert step4_verdict(pf=1.54, n=6)["verdict"] == "HOLD (n<10)"
    assert step4_verdict(pf=1.1, n=15)["verdict"] == "HOLD (PF<1.3)"
    assert step4_verdict(pf=0.8, n=15)["verdict"] == "FAIL (PF<1.0)"
    assert step4_verdict(pf=None, n=0)["verdict"] == "NO TRADES"


def test_symbol_breakdown_filters_bot_and_window():
    from tools.fleet_review import symbol_stats
    trades = [_t("Scalp", 2.0, symbol="ETHUSDT"),
               _t("Scalp", -1.0, symbol="ETHUSDT"),
               _t("Scalp", 1.0, symbol="BTCUSDT"),
               _t("Breakout", 9.0, symbol="ETHUSDT")]
    rows = symbol_stats(trades, bot="Scalp", days=14)
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["ETHUSDT"]["n"] == 2
    assert by_sym["ETHUSDT"]["net"] == pytest.approx(1.0)
    assert by_sym["BTCUSDT"]["n"] == 1
    assert "Breakout" not in str(rows)


# ─── Entry-blocker histogram (Jul 24 — momentum's 3-week silence needs
#     an at-a-glance answer in every review, not a dashboard visit) ─────────

def _sig(blocked_by, hours_ago=1.0, would_enter=False):
    checked = (datetime.now(timezone.utc)
                - timedelta(hours=hours_ago)).isoformat()
    return {"blocked_by": blocked_by, "would_enter": would_enter,
            "checked_at": checked}


def test_blocked_by_rows_groups_and_sorts():
    from tools.fleet_review import blocked_by_rows
    rows = blocked_by_rows({
        "BTC":     _sig("btc_filter"),
        "ADA_4H":  _sig("btc_filter"),
        "INJ_4H":  _sig("trend"),
        "SUI_1D":  _sig(None, would_enter=True),
    })
    by_reason = {r["reason"]: r for r in rows}
    assert by_reason["btc_filter"]["n"] == 2
    assert set(by_reason["btc_filter"]["assets"]) == {"BTC", "ADA_4H"}
    assert by_reason["trend"]["n"] == 1
    assert by_reason["WOULD_ENTER"]["n"] == 1
    assert rows[0]["reason"] == "btc_filter"      # sorted by count desc


def test_blocked_by_rows_drops_stale_entries():
    """Relic signal_status rows from parked bots' assets must not
    pollute the histogram — only recently-checked entries count."""
    from tools.fleet_review import blocked_by_rows
    rows = blocked_by_rows({
        "BTC":    _sig("btc_filter", hours_ago=1.0),
        "OLD_1H": _sig("trend", hours_ago=200.0),
    }, max_age_h=24.0)
    reasons = {r["reason"] for r in rows}
    assert "trend" not in reasons
    assert "btc_filter" in reasons
