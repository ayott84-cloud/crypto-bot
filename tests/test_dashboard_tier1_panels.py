"""Dashboard Tier 1.3 + 1.4 (redesign options doc).

1.3 Exit-reason distribution (trailing 14d, per bot) with the P4
    runbook thresholds drawn in: SL-share > 60% = brackets too tight;
    Time-Limit share > 40% = entries firing into drift.
1.4 Revalidation gate tracker: each bot's position in the P4 pipeline,
    driven by revalidation_status.json.

Run: python -m pytest tests/test_dashboard_tier1_panels.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

import dashboard
from dashboard_renderer import render


def _t(bot, reason, days_ago=1, pnl=-1.0):
    closed = (datetime.now() - timedelta(days=days_ago)).isoformat()
    return {"bot": bot, "result": "LOSS" if pnl < 0 else "WIN",
            "net_pnl": pnl, "exit_reason": reason, "date_closed": closed,
            "exit_price": 100.0}


# ─── Tier 1.3 — exit-reason distribution ───────────────────────────────────

def test_exit_reason_panel_shares_and_flags():
    trades = (
        [_t("Scalp", "SL Hit") for _ in range(7)]
        + [_t("Scalp", "TP Hit", pnl=2.0) for _ in range(2)]
        + [_t("Scalp", "Time Limit") for _ in range(1)]
    )
    panel = dashboard._v2_exit_reason_panel(trades)
    scalp = next(r for r in panel["bots"] if r["label"] == "Scalp")
    assert scalp["total"] == 10
    assert scalp["sl_share"] == 70
    assert scalp["tl_share"] == 10
    assert scalp["sl_flag"] is True        # 70% > 60% threshold
    assert scalp["tl_flag"] is False
    # segments cover 100%
    assert sum(s["pct"] for s in scalp["segments"]) == pytest.approx(100, abs=1)


def test_exit_reason_panel_old_trades_excluded():
    trades = [_t("Scalp", "SL Hit", days_ago=20)]
    panel = dashboard._v2_exit_reason_panel(trades)
    assert all(r["label"] != "Scalp" for r in panel["bots"])


def test_exit_reason_panel_renders():
    trades = [_t("Breakout", "Trailing Exit", pnl=3.0),
               _t("Breakout", "SL Hit")]
    panel = dashboard._v2_exit_reason_panel(trades)
    html = render("components/exit_reason_panel.html.j2",
                    {"exit_reasons": panel})
    assert "Exit reasons" in html
    assert "Trailing Exit" in html


# ─── Tier 1.4 — revalidation gate tracker ──────────────────────────────────

def test_gate_tracker_reads_status_file():
    panel = dashboard._v2_gate_tracker()
    assert panel["available"] is True
    bots = {r["bot"] for r in panel["rows"]}
    assert "scalp" in bots and "breakout" in bots and "crossover" in bots
    scalp = next(r for r in panel["rows"] if r["bot"] == "scalp")
    assert 0 <= scalp["step"] <= 6
    assert len(scalp["steps"]) == 7
    assert scalp["steps"][scalp["step"]]["current"] is True
    assert scalp["note"]


def test_gate_tracker_graceful_on_missing_file(monkeypatch):
    monkeypatch.setattr(dashboard, "_REVALIDATION_STATUS_FILE",
                          BOT_DIR / "nonexistent-status.json")
    panel = dashboard._v2_gate_tracker()
    assert panel["available"] is False


def test_gate_tracker_renders():
    panel = dashboard._v2_gate_tracker()
    html = render("components/gate_tracker_panel.html.j2",
                    {"gate_tracker": panel})
    assert "Revalidation pipeline" in html
    assert "Micro-live" in html
