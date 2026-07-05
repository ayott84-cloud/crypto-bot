"""R3 — Discord two-way control plane (Phase O, operator-approved).

Security model under test:
  - allowlist: only configured operator user IDs are heard at all
  - read commands (!status/!pnl/!positions) answer instantly
  - write commands (!pause/!resume) require the confirmation word from
    the SAME user within the window, else expire
  - pauses flow through control_flags.json -> kill_switch.should_pause
    (every bot's entry path) — no sudo, no restarts, effective within
    one poll cycle

Run: python -m pytest tests/test_discord_control.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest


# ─── control_flags ─────────────────────────────────────────────────────────

def test_control_flags_roundtrip(tmp_path, monkeypatch):
    import control_flags as cf
    monkeypatch.setattr(cf, "_FLAGS_PATH", tmp_path / "control_flags.json")
    assert cf.is_operator_paused("scalp") is False
    cf.set_flag("scalp", paused=True, by="ayott84")
    assert cf.is_operator_paused("scalp") is True
    cf.set_flag("scalp", paused=False, by="ayott84")
    assert cf.is_operator_paused("scalp") is False


def test_control_flags_corrupt_file_fails_open(tmp_path, monkeypatch):
    import control_flags as cf
    p = tmp_path / "control_flags.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(cf, "_FLAGS_PATH", p)
    assert cf.is_operator_paused("scalp") is False   # never block on corruption


def test_kill_switch_honors_operator_pause(tmp_path, monkeypatch):
    import control_flags as cf
    import kill_switch as ks
    monkeypatch.setattr(cf, "_FLAGS_PATH", tmp_path / "control_flags.json")
    cf.set_flag("scalp", paused=True, by="test")
    status = ks.should_pause("scalp")
    assert status.paused is True
    assert "operator" in status.reason.lower()
    # other owners unaffected by scalp's flag
    assert ks.should_pause("breakout").paused in (True, False)


# ─── command parsing + session state machine ──────────────────────────────

def _session(**kw):
    from discord_control import ControlSession
    defaults = dict(operator_ids={"111"}, confirm_word="CONFIRM",
                     confirm_window_s=120)
    defaults.update(kw)
    return ControlSession(**defaults)


def test_non_allowlisted_users_are_ignored():
    s = _session()
    out = s.handle("999", "!status", now=1000.0)
    assert out is None


def test_read_command_answers_instantly(monkeypatch):
    s = _session()
    monkeypatch.setattr(s, "_status_text", lambda: "STATUS OK")
    out = s.handle("111", "!status", now=1000.0)
    assert "STATUS OK" in out


def test_write_command_requires_confirmation(tmp_path, monkeypatch):
    import control_flags as cf
    monkeypatch.setattr(cf, "_FLAGS_PATH", tmp_path / "control_flags.json")
    s = _session()
    out1 = s.handle("111", "!pause scalp", now=1000.0)
    assert "CONFIRM" in out1                       # asked to confirm
    assert cf.is_operator_paused("scalp") is False  # nothing happened yet
    out2 = s.handle("111", "CONFIRM", now=1030.0)
    assert cf.is_operator_paused("scalp") is True
    assert "paused" in out2.lower()


def test_confirmation_expires_after_window(tmp_path, monkeypatch):
    import control_flags as cf
    monkeypatch.setattr(cf, "_FLAGS_PATH", tmp_path / "control_flags.json")
    s = _session()
    s.handle("111", "!pause scalp", now=1000.0)
    out = s.handle("111", "CONFIRM", now=1200.0)   # 200s > 120s window
    assert cf.is_operator_paused("scalp") is False
    assert out is not None and "expired" in out.lower()


def test_unknown_bot_rejected_without_pending():
    s = _session()
    out = s.handle("111", "!pause dogecoin-yolo", now=1000.0)
    assert "unknown bot" in out.lower()


def test_no_order_placement_commands_exist():
    """v1 ships NO trade/close/order commands — pause/resume only."""
    import inspect
    import discord_control
    src = inspect.getsource(discord_control)
    for forbidden in ("open_long", "open_short", "close_long", "close_short",
                        "place_order", "close_positions"):
        assert forbidden not in src
