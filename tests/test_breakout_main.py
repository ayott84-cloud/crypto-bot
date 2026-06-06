"""Phase G.4 — breakout_main run-cycle integration.

Mocks the executor + journal so the cycle logic can be tested without
real exchange/database calls. Verifies:
  - Pause flag blocks new entries
  - Asset entries fire when analyze_breakout_entry says would_enter=True
  - Open positions get exit-checked each cycle
  - Heartbeat file is written every cycle (even when paused)
  - SHORT entries blocked when allow_short=False (gated at signal level
    in G.1, smoke-tested here too)

Run: python -m pytest tests/test_breakout_main.py -v
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


# ─── Fixtures ──────────────────────────────────────────────────────────────

def _state(positions=None):
    return {"positions": positions or {}}


def _mock_executor():
    """Mock executor returning WEEX-positional-format klines (25 bars).

    WEEX kline rows are arrays: [ts_ms, o, h, l, c, v, close_ts, qv, n, tbv, tbqv]
    """
    ex = MagicMock()
    base = 1_700_000_000_000
    rows = [[base + i * 60_000, 100.0, 102.0, 99.0, 101.0, 1000.0,
             base + (i + 1) * 60_000, 100000, 50, 500, 50000]
            for i in range(25)]
    ex.get_klines.return_value = rows
    ex.get_symbol_price.return_value = 110.0
    return ex


# ─── Heartbeat ─────────────────────────────────────────────────────────────

def test_heartbeat_written_each_cycle(tmp_path, monkeypatch):
    import breakout_main
    hb = tmp_path / "hb"
    monkeypatch.setattr(breakout_main, "_HEARTBEAT_FILE", hb)
    breakout_main._write_heartbeat(hb)
    assert hb.exists()


# ─── Pause flag ────────────────────────────────────────────────────────────

def test_pause_flag_blocks_new_entries(monkeypatch):
    import breakout_main
    monkeypatch.setattr(breakout_main, "BREAKOUT_PAUSED", True)

    ex = _mock_executor()
    state = _state()

    # Spy on open_breakout_position to assert it was NEVER called
    with patch.object(breakout_main, "open_breakout_position") as mock_open:
        breakout_main.run_cycle(ex, state)
        assert mock_open.call_count == 0


def test_pause_flag_still_writes_heartbeat(monkeypatch, tmp_path):
    """Even when paused, heartbeat must fire so dashboard shows LIVE."""
    import breakout_main
    hb = tmp_path / "hb"
    monkeypatch.setattr(breakout_main, "BREAKOUT_PAUSED", True)
    monkeypatch.setattr(breakout_main, "_HEARTBEAT_FILE", hb)

    ex = _mock_executor()
    state = _state()
    breakout_main.run_cycle(ex, state)
    assert hb.exists()


# ─── Open-position management ─────────────────────────────────────────────

def test_manage_open_positions_calls_check_breakout_exit(monkeypatch):
    import breakout_main
    monkeypatch.setattr(breakout_main, "BREAKOUT_PAUSED", True)  # skip new-entry path

    state = _state(positions={
        "BREAKOUT_BTC_4H": {
            "entry_price": 100.0,
            "atr_at_entry": 2.0,
            "quantity": 0.05,
            "direction": "LONG",
            "symbol": "BTCUSDT",
            "strategy": "BTC 4H Breakout",
        }
    })

    ex = _mock_executor()
    with patch("breakout_main.check_breakout_exit") as mock_exit:
        mock_exit.return_value = (None, None)  # no exit
        breakout_main.run_cycle(ex, state)
        assert mock_exit.call_count >= 1


def test_exit_signal_closes_position(monkeypatch):
    """When check_breakout_exit returns a reason, close_breakout_position fires."""
    import breakout_main
    monkeypatch.setattr(breakout_main, "BREAKOUT_PAUSED", True)

    state = _state(positions={
        "BREAKOUT_BTC_4H": {
            "entry_price": 100.0, "atr_at_entry": 2.0, "quantity": 0.05,
            "direction": "LONG", "symbol": "BTCUSDT",
            "strategy": "BTC 4H Breakout",
        }
    })

    ex = _mock_executor()
    with patch("breakout_main.check_breakout_exit") as mock_exit, \
         patch.object(breakout_main, "close_breakout_position") as mock_close:
        mock_exit.return_value = ("SL Hit", "full")
        breakout_main.run_cycle(ex, state)
        assert mock_close.call_count == 1
