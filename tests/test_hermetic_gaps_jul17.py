"""Jul 17 2026 — two gaps found via the droplet heartbeat inventory.

1. The Jul 16 droplet pytest run (15:50-16:01 UTC) recreated 0-byte
   .pair_heartbeat / .reversal_heartbeat relics in BOT_DIR: the hermetic
   conftest redirected journal/state/flags/stamps but NOT the bot mains'
   heartbeat files, signal logs, or the notifier credentials — on the
   droplet, .env carries the REAL Discord webhook, so an unmocked notify
   call in a test would ping production Discord.

2. Momentum (main.py — the original bot) writes NO heartbeat at all.
   Every other bot does; the risk sentinel is blind to a wedged
   momentum process.

Run: python -m pytest tests/test_hermetic_gaps_jul17.py -v
"""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


# ─── 1. hermetic coverage for heartbeats / signal logs / notifier ─────────

_HEARTBEAT_GLOBALS = [
    ("main",           "_HEARTBEAT_FILE"),
    ("scalp_main",     "_HEARTBEAT_FILE"),
    ("breakout_main",  "_HEARTBEAT_FILE"),
    ("crossover_main", "_HEARTBEAT_FILE"),
    ("pair_main",      "_HEARTBEAT_FILE"),
    ("reversal_main",  "_HEARTBEAT_FILE"),
    ("whale_main",     "_HEARTBEAT_FILE"),
    ("funding_main",   "FUNDING_HEARTBEAT"),
]

_SIGNAL_LOG_GLOBALS = [
    ("whale_main",   "WHALE_SIGNAL_LOG"),
    ("funding_main", "FUNDING_SIGNAL_LOG"),
]


@pytest.mark.parametrize("mod_name,attr", _HEARTBEAT_GLOBALS)
def test_heartbeat_paths_redirected_out_of_bot_dir(mod_name, attr):
    """During tests, no bot main's heartbeat global may point into the
    real BOT_DIR — a run_cycle test without its own patch must land in
    tmp, not recreate production relic files."""
    mod = importlib.import_module(mod_name)
    p = Path(getattr(mod, attr))
    assert p.parent != BOT_DIR, f"{mod_name}.{attr} still points at BOT_DIR"


@pytest.mark.parametrize("mod_name,attr", _SIGNAL_LOG_GLOBALS)
def test_signal_logs_redirected_out_of_bot_dir(mod_name, attr):
    mod = importlib.import_module(mod_name)
    p = Path(getattr(mod, attr))
    assert p.parent != BOT_DIR, f"{mod_name}.{attr} still points at BOT_DIR"


def test_notifier_credentials_blanked():
    """The suite must behave identically on a dev box (no .env) and the
    droplet (real webhook + SMTP in .env): both send paths no-op unless
    a test explicitly patches its own fake."""
    import notifier
    assert notifier.DISCORD_WEBHOOK_URL == ""
    assert not notifier.SMTP_PASS
    assert not notifier.SMTP_USER


# ─── 2. momentum heartbeat ────────────────────────────────────────────────

def test_momentum_heartbeat_file_named_for_owner():
    """Sentinel derives owner from the filename: '.momentum_heartbeat'
    -> owner 'momentum'."""
    import main
    assert main._HEARTBEAT_FILE.name == ".momentum_heartbeat"


def test_momentum_write_heartbeat_touches_file(tmp_path, monkeypatch):
    import main
    hb = tmp_path / ".momentum_heartbeat"
    monkeypatch.setattr(main, "_HEARTBEAT_FILE", hb)
    main._write_heartbeat()
    assert hb.exists()


def test_momentum_run_loop_beats_every_cycle():
    """The while-True cycle body in run() must call _write_heartbeat
    before per-asset work — a wedged momentum must go stale within the
    sentinel's 30-min bar (poll interval is 300s)."""
    import main
    src = inspect.getsource(main.run)
    assert "_write_heartbeat()" in src
