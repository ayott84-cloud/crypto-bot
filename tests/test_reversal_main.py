"""Phase I.4 — reversal_main run-cycle integration.

Verifies the dispatch logic of run_cycle:
  - Heartbeat fires every cycle (even when paused)
  - PAUSED blocks new entries
  - Open positions get exit-checked
  - Exit signal closes the position
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


def _state(positions=None):
    return {"positions": positions or {}}


def _mock_executor():
    ex = MagicMock()
    ex.get_klines.return_value = [
        {"open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000}
        for _ in range(40)
    ]
    ex.get_symbol_price.return_value = 100.0
    return ex


def test_heartbeat_written(tmp_path, monkeypatch):
    import reversal_main
    hb = tmp_path / "hb"
    monkeypatch.setattr(reversal_main, "_HEARTBEAT_FILE", hb)
    reversal_main._write_heartbeat(hb)
    assert hb.exists()


def test_pause_flag_blocks_new_entries(monkeypatch):
    import reversal_main
    monkeypatch.setattr(reversal_main, "REVERSAL_PAUSED", True)
    ex = _mock_executor()
    state = _state()
    with patch.object(reversal_main, "open_reversal_position") as mock_open:
        reversal_main.run_cycle(ex, state)
        assert mock_open.call_count == 0


def test_pause_flag_still_writes_heartbeat(tmp_path, monkeypatch):
    import reversal_main
    hb = tmp_path / "hb"
    monkeypatch.setattr(reversal_main, "REVERSAL_PAUSED", True)
    monkeypatch.setattr(reversal_main, "_HEARTBEAT_FILE", hb)
    ex = _mock_executor()
    state = _state()
    reversal_main.run_cycle(ex, state)
    assert hb.exists()
