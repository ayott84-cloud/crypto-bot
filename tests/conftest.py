"""Global hermetic guard — no test may touch production data files.

Background (Jul 16 2026): the suite was run twice ON THE DROPLET during
P4 deploys with nothing redirecting the write-path module globals. Any
test writing through the real journal / position_manager landed in the
production trades.db / state.json (the phantom breakout row with entry
~100.0 BTCUSDT is consistent with a fixture position leaking into live
state and being closed at market by the running bot).

This autouse fixture redirects every known write-path module global to
a per-test tmp directory. Tests that patch these paths themselves simply
override the redirect — both layers restore on teardown. Read-only
files (config constants, revalidation_status.json) are NOT redirected.

Verified by tests/test_hermetic_suite.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))


@pytest.fixture(autouse=True)
def _hermetic_data_paths(tmp_path, monkeypatch):
    import journal
    import position_manager
    import control_flags
    import routine_stamps

    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "trades.db")
    monkeypatch.setattr(journal, "LEGACY_JSONL", tmp_path / "trades.jsonl")
    monkeypatch.setattr(journal, "LEGACY_JSONL_MIGRATED",
                          tmp_path / "trades.jsonl.migrated")
    # Schema is created lazily once per process; force re-init so each
    # test's fresh tmp DB gets the schema.
    monkeypatch.setattr(journal, "_initialized", False)

    monkeypatch.setattr(position_manager, "STATE_FILE",
                          tmp_path / "state.json")
    monkeypatch.setattr(control_flags, "_FLAGS_PATH",
                          tmp_path / "control_flags.json")
    monkeypatch.setattr(routine_stamps, "_STAMPS_PATH",
                          tmp_path / ".routine_stamps.json")

    # Jul 17 2026 extension: the Jul 16 droplet pytest run recreated
    # 0-byte .pair_heartbeat / .reversal_heartbeat relics in BOT_DIR —
    # heartbeats, signal logs, and notifier credentials were not covered.
    # Bot mains import heavy deps (pandas); skip any that can't import.
    _file_globals = [
        ("main",           "_HEARTBEAT_FILE",    ".momentum_heartbeat"),
        ("scalp_main",     "_HEARTBEAT_FILE",    ".scalp_heartbeat"),
        ("breakout_main",  "_HEARTBEAT_FILE",    ".breakout_heartbeat"),
        ("crossover_main", "_HEARTBEAT_FILE",    ".crossover_heartbeat"),
        ("pair_main",      "_HEARTBEAT_FILE",    ".pair_heartbeat"),
        ("reversal_main",  "_HEARTBEAT_FILE",    ".reversal_heartbeat"),
        ("whale_main",     "_HEARTBEAT_FILE",    ".whale_heartbeat"),
        ("funding_main",   "FUNDING_HEARTBEAT",  ".funding_heartbeat"),
        ("whale_main",     "WHALE_SIGNAL_LOG",   "whale_signals.jsonl"),
        ("funding_main",   "FUNDING_SIGNAL_LOG", "funding_signals.jsonl"),
    ]
    import importlib
    for mod_name, attr, fname in _file_globals:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, tmp_path / fname)

    # Notifier: a dev box has no .env (both send paths already no-op);
    # the droplet's .env carries the REAL webhook + SMTP creds. Blank
    # them so the suite behaves identically everywhere — tests exercising
    # send paths patch their own fakes, which override this.
    try:
        import notifier
        monkeypatch.setattr(notifier, "DISCORD_WEBHOOK_URL", "")
        for cred in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "NOTIFY_EMAIL"):
            if hasattr(notifier, cred):
                monkeypatch.setattr(notifier, cred, "")
    except Exception:
        pass
    yield
