"""Unit tests for whale_main._write_heartbeat helper.

Phase A.1 of the comprehensive enhancement plan. The helper must write an
ISO-8601 UTC timestamp to a given path, and must not raise on filesystem
errors (the dashboard relies on the file's mtime, not its content, so a
transient write failure should be logged-and-swallowed rather than crashing
the cycle).

Run: python -m pytest tests/test_whale_heartbeat.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

from whale_main import _write_heartbeat


def test_write_heartbeat_creates_file(tmp_path):
    target = tmp_path / ".whale_heartbeat"
    _write_heartbeat(target)
    assert target.exists()


def test_write_heartbeat_writes_iso_utc_timestamp(tmp_path):
    target = tmp_path / ".whale_heartbeat"
    before = datetime.now(timezone.utc)
    _write_heartbeat(target)
    after = datetime.now(timezone.utc)

    content = target.read_text(encoding="utf-8").strip()
    parsed = datetime.fromisoformat(content)

    # ISO-8601 must include tz info
    assert parsed.tzinfo is not None
    # Within the wall-clock window of the call
    assert before <= parsed <= after


def test_write_heartbeat_overwrites_existing(tmp_path):
    target = tmp_path / ".whale_heartbeat"
    target.write_text("stale", encoding="utf-8")
    _write_heartbeat(target)
    content = target.read_text(encoding="utf-8").strip()
    # Stale content gone, replaced with a parseable ISO timestamp
    datetime.fromisoformat(content)


def test_write_heartbeat_swallows_filesystem_errors(tmp_path):
    """If the path is unwritable, the helper must log+swallow, not raise.

    The cycle calls this in a try/finally — an exception here would propagate
    out of finally and mask the original cause of the cycle termination.
    """
    # A path whose parent does not exist → write fails
    bad_path = tmp_path / "does" / "not" / "exist" / ".whale_heartbeat"
    # Must not raise
    _write_heartbeat(bad_path)
    assert not bad_path.exists()
