"""Phase F.4 — pair_main run cycle.

Mocks the executor + journal so the dual-leg open/close logic can be
tested without real exchange calls. Verifies:
  - Pause flag blocks new entries
  - Open positions get checked for exit each cycle
  - Exit signal closes BOTH legs (not just one)
  - Heartbeat fires even when paused

Run: python -m pytest tests/test_pair_main.py -v
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


def _mock_executor_with_klines():
    """Mock executor returning WEEX-positional-format klines (35 flat bars).

    WEEX kline rows are arrays: [ts_ms, o, h, l, c, v, close_ts, qv, n, tbv, tbqv]
    """
    ex = MagicMock()
    def _klines(symbol, interval, count):
        base = 1_700_000_000_000
        return [[base + i * 60_000, 100.0, 101.0, 99.0, 100.0, 1000.0,
                 base + (i + 1) * 60_000, 100000, 50, 500, 50000]
                for i in range(count)]
    ex.get_klines.side_effect = _klines
    ex.get_symbol_price.return_value = 100.0
    return ex


def test_heartbeat_written_each_cycle(tmp_path, monkeypatch):
    import pair_main
    hb = tmp_path / "hb"
    monkeypatch.setattr(pair_main, "_HEARTBEAT_FILE", hb)
    pair_main._write_heartbeat(hb)
    assert hb.exists()


def test_pause_flag_blocks_new_entries(monkeypatch):
    import pair_main
    monkeypatch.setattr(pair_main, "PAIR_PAUSED", True)
    ex = _mock_executor_with_klines()
    state = _state()
    with patch.object(pair_main, "open_pair_position") as mock_open:
        pair_main.run_cycle(ex, state)
        assert mock_open.call_count == 0


def test_pause_flag_still_writes_heartbeat(tmp_path, monkeypatch):
    import pair_main
    hb = tmp_path / "hb"
    monkeypatch.setattr(pair_main, "PAIR_PAUSED", True)
    monkeypatch.setattr(pair_main, "_HEARTBEAT_FILE", hb)
    ex = _mock_executor_with_klines()
    state = _state()
    pair_main.run_cycle(ex, state)
    assert hb.exists()


def test_exit_signal_closes_both_legs(monkeypatch):
    """Pair trade is one logical unit — exit closes both legs together."""
    import pair_main
    monkeypatch.setattr(pair_main, "PAIR_PAUSED", True)

    state = _state(positions={
        "PAIR_ETHBTC_LONG_LEG": {
            "entry_price": 2000.0, "quantity": 0.25,
            "direction": "LONG", "symbol": "ETHUSDT",
            "strategy": "Pair ETH/BTC", "bars_held": 2,
            "entry_ratio": 0.05,
        },
        "PAIR_ETHBTC_SHORT_LEG": {
            "entry_price": 40000.0, "quantity": 0.0125,
            "direction": "SHORT", "symbol": "BTCUSDT",
            "strategy": "Pair ETH/BTC", "bars_held": 2,
            "entry_ratio": 0.05,
        },
    })

    ex = _mock_executor_with_klines()
    with patch("pair_main.check_pair_exit") as mock_exit, \
         patch.object(pair_main, "close_pair_position") as mock_close:
        mock_exit.return_value = ("Z Reverted", "full")
        pair_main.run_cycle(ex, state)
        assert mock_close.call_count == 1  # one helper call, closes BOTH legs
