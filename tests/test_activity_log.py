"""Phase J.10 — per-bot activity log (journalctl tail at build time)."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

import dashboard


# ─── _parse_journal_iso_line ──────────────────────────────────────────────

def test_parse_entry_line():
    line = ("2026-06-07T16:10:23-0500 droplet crypto-momentum[1234]: "
            "ENTRY ETH_4H LONG @ 2845.30")
    row = dashboard._parse_journal_iso_line(line)
    assert row is not None
    assert row["ts"].startswith("2026-06-07T16:10:23")
    assert row["level"] == "signal"
    assert "ENTRY ETH_4H" in row["msg"]


def test_parse_exit_line_as_signal():
    line = ("2026-06-07T16:11:00-0500 droplet crypto-momentum[1234]: "
            "EXIT BTC_4H reason=TP1 net_pnl=+12.50")
    row = dashboard._parse_journal_iso_line(line)
    assert row["level"] == "signal"


def test_parse_error_line_as_error_level():
    line = ("2026-06-07T16:12:00-0500 droplet crypto-whale[999]: "
            "ERROR HL API timeout — retrying")
    row = dashboard._parse_journal_iso_line(line)
    assert row["level"] == "error"


def test_parse_drops_heartbeat_noise():
    line = ("2026-06-07T16:13:00-0500 droplet crypto-momentum[1234]: "
            "heartbeat written")
    assert dashboard._parse_journal_iso_line(line) is None


def test_parse_drops_debug_lines():
    line = ("2026-06-07T16:14:00-0500 droplet crypto-momentum[1234]: "
            "DEBUG iteration tick complete")
    assert dashboard._parse_journal_iso_line(line) is None


def test_parse_drops_uninteresting_chatter():
    line = ("2026-06-07T16:15:00-0500 droplet crypto-momentum[1234]: "
            "loaded 16 assets from config")
    # No ENTRY/EXIT/SIGNAL/ERROR/etc keywords → filtered out
    assert dashboard._parse_journal_iso_line(line) is None


def test_parse_handles_blank_message():
    line = "2026-06-07T16:16:00-0500 droplet crypto-momentum[1234]:"
    assert dashboard._parse_journal_iso_line(line) is None


def test_parse_handles_unparseable_line():
    assert dashboard._parse_journal_iso_line("") is None
    assert dashboard._parse_journal_iso_line("garbage no timestamp") is None


# ─── _v2_bot_activity_log graceful failure ────────────────────────────────

def test_activity_log_returns_empty_when_journalctl_unavailable(monkeypatch):
    """On Windows dev box / missing systemd, the helper must return [] not raise."""
    dashboard._activity_log_cache_clear()
    import subprocess as _sp

    def _raise(*args, **kwargs):
        raise FileNotFoundError("journalctl missing")

    monkeypatch.setattr(_sp, "run", _raise)
    rows = dashboard._v2_bot_activity_log("momentum")
    assert rows == []


def test_activity_log_caches_within_ttl(monkeypatch):
    dashboard._activity_log_cache_clear()
    import subprocess as _sp
    call_count = {"n": 0}

    class FakeResult:
        returncode = 0
        stdout = ""

    def _fake_run(*args, **kwargs):
        call_count["n"] += 1
        return FakeResult()

    monkeypatch.setattr(_sp, "run", _fake_run)
    dashboard._v2_bot_activity_log("momentum")
    dashboard._v2_bot_activity_log("momentum")
    assert call_count["n"] == 1


def test_activity_log_parses_real_output(monkeypatch):
    dashboard._activity_log_cache_clear()
    import subprocess as _sp

    sample = "\n".join([
        "2026-06-07T16:10:23-0500 d crypto-momentum[1]: ENTRY ETH_4H LONG @ 2845",
        "2026-06-07T16:10:24-0500 d crypto-momentum[1]: heartbeat written",
        "2026-06-07T16:11:00-0500 d crypto-momentum[1]: EXIT BTC_4H TP1 +12.50",
        "2026-06-07T16:11:30-0500 d crypto-momentum[1]: ERROR WEEX 5xx — retrying",
    ])

    class FakeResult:
        returncode = 0
        stdout = sample

    monkeypatch.setattr(_sp, "run", lambda *a, **k: FakeResult())
    rows = dashboard._v2_bot_activity_log("momentum")
    assert len(rows) == 3  # heartbeat filtered
    # Newest-first
    assert "ERROR" in rows[0]["msg"]
    assert rows[0]["level"] == "error"


# ─── Tab include audit ────────────────────────────────────────────────────

def test_every_bot_tab_includes_activity_log_partial():
    """Every bot tab must consume its activity_logs entry — otherwise the
    journalctl read at build time is wasted work."""
    template_dir = Path(dashboard.__file__).parent / "templates" / "tabs"
    bot_tabs = ("momentum.html.j2", "whale.html.j2", "funding.html.j2",
                 "breakout.html.j2", "pair.html.j2", "reversal.html.j2")
    for fname in bot_tabs:
        text = (template_dir / fname).read_text(encoding="utf-8")
        assert "bot_activity_log.html.j2" in text, (
            f"{fname} missing activity-log include")


def test_context_includes_activity_logs_for_every_bot():
    ctx = dashboard._v2_test_context([])
    assert "activity_logs" in ctx
    for bot in ("momentum", "whale", "funding", "breakout", "pair", "reversal"):
        assert bot in ctx["activity_logs"]


def test_activity_log_template_renders_empty_state():
    pytest.importorskip("jinja2")
    from dashboard_renderer import render
    html = render("base.html.j2", dashboard._v2_test_context([]))
    # All bots empty → empty-state copy appears (at least once per bot tab)
    assert "No recent entries" in html
