"""R2 — droplet-native scheduled routines (risk sentinel + alpha brief).

Pure-function tests only; the Discord sender is monkeypatched, nothing
touches the network or the real journal.

Run: python -m pytest tests/test_routine_scripts.py -v
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


# ─── risk_check ────────────────────────────────────────────────────────────

def test_heartbeat_staleness_classifier(tmp_path):
    from tools.risk_check import classify_heartbeats
    fresh = tmp_path / ".scalp_heartbeat"
    stale = tmp_path / ".breakout_heartbeat"
    fresh.touch()
    stale.touch()
    old = time.time() - 3600
    import os
    os.utime(stale, (old, old))
    rows = classify_heartbeats([fresh, stale], stale_after_s=1800)
    by_name = {r["name"]: r for r in rows}
    assert by_name[".scalp_heartbeat"]["stale"] is False
    assert by_name[".breakout_heartbeat"]["stale"] is True


def test_parked_bots_heartbeats_are_ignored(tmp_path, monkeypatch):
    """A PARKED bot (revalidation step 0 — reversal, crossover, pair)
    has no duty to heartbeat: its service may be disabled entirely. Its
    relic heartbeat file must not alarm hourly (the .reversal_heartbeat
    false positive of Jul 5-6)."""
    import tools.risk_check as rc
    monkeypatch.setattr(rc, "_parked_owners",
                          lambda: {"reversal", "crossover", "pair"})
    parked = tmp_path / ".reversal_heartbeat"
    live = tmp_path / ".scalp_heartbeat"
    parked.touch()
    live.touch()
    import os
    old = time.time() - 90000
    os.utime(parked, (old, old))
    os.utime(live,   (old, old))
    rows = rc.classify_heartbeats([parked, live], stale_after_s=1800)
    names = {r["name"] for r in rows}
    assert ".reversal_heartbeat" not in names    # parked → excluded
    assert ".scalp_heartbeat" in names            # live bot still flags
    assert all(r["stale"] for r in rows)


def test_parked_owners_reads_status_file():
    from tools.risk_check import _parked_owners
    parked = _parked_owners()
    # revalidation_status.json marks these step 0 as of Jul 2026
    assert {"reversal", "crossover", "pair"} <= parked
    assert "scalp" not in parked


def test_positions_missing_sl_detector():
    from tools.risk_check import positions_missing_sl
    positions = {
        # P5a-era position with its bracket persisted — fine
        "SCALP_ETH_5M":  {"bracket_kind": "atr", "sl_price": 2450.0},
        # P5a-era position that SHOULD have a stop but doesn't — flag
        "SCALP_BTC_5M":  {"bracket_kind": "atr", "sl_price": None},
        "CROSSOVER_X":   {"exit_kind": "invalidation"},   # sl missing — flag
        # legacy position (pre-P5a, no marker fields) — cannot judge, skip
        "BREAKOUT_OLD":  {"entry_price": 1.0},
    }
    flagged = positions_missing_sl(positions)
    assert set(flagged) == {"SCALP_BTC_5M", "CROSSOVER_X"}


def test_build_issues_flags_breach_and_stale():
    from tools.risk_check import build_issues
    issues = build_issues(
        ks_summary={"scalp": {"paused": True, "reason": "daily drawdown"}},
        daily={"pnl": -340.0, "threshold": -150.0, "breached": True},
        heartbeats=[{"name": ".scalp_heartbeat", "age_s": 4000, "stale": True}],
        missing_sl=["SCALP_BTC_5M"],
    )
    text = " | ".join(issues)
    assert "scalp" in text
    assert "-340" in text or "340" in text
    assert ".scalp_heartbeat" in text
    assert "SCALP_BTC_5M" in text


def test_build_issues_empty_when_healthy():
    from tools.risk_check import build_issues
    assert build_issues(
        ks_summary={"scalp": {"paused": False, "reason": ""}},
        daily={"pnl": 12.0, "threshold": -150.0, "breached": False},
        heartbeats=[{"name": ".scalp_heartbeat", "age_s": 60, "stale": False}],
        missing_sl=[],
    ) == []


# ─── alpha_brief ───────────────────────────────────────────────────────────

def test_brief_last24_section():
    from tools.alpha_brief import section_last24
    from datetime import datetime, timedelta
    closed = (datetime.now() - timedelta(hours=2)).isoformat()
    trades = [
        {"bot": "Scalp", "result": "WIN",  "net_pnl": 3.5,
          "date_closed": closed, "exit_price": 1.0},
        {"bot": "Scalp", "result": "LOSS", "net_pnl": -1.5,
          "date_closed": closed, "exit_price": 1.0},
    ]
    lines = section_last24(trades)
    joined = " ".join(lines)
    assert "Scalp" in joined
    assert "2" in joined            # trade count
    assert "+2.00" in joined or "2.00" in joined


def test_brief_field_chunking_respects_discord_limit():
    from tools.alpha_brief import chunk_field
    lines = [f"line {i} " + "x" * 60 for i in range(40)]
    chunks = chunk_field(lines, limit=1024)
    assert all(len(c) <= 1024 for c in chunks)
    assert "".join(chunks).count("line 39") == 1


def test_brief_never_sends_without_webhook(monkeypatch):
    """The composer must route through notifier's sender (which no-ops
    without a webhook) — never its own HTTP."""
    import tools.alpha_brief as ab
    sent = {}
    def fake_send(title, description, color, fields):
        sent["title"] = title
        return True
    monkeypatch.setattr(ab, "_send_embed", fake_send)
    ab.send_brief([("Fleet", ["all quiet"])])
    assert "Alpha Brief" in sent["title"]
