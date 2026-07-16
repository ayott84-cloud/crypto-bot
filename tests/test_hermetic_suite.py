"""The test suite must be hermetic — no test may write to the
production trades.db / state.json / control flags / routine stamps.

Background (Jul 16 2026): the full pytest suite was run twice ON THE
DROPLET during P4 deploys. Nothing global redirected journal.DB_PATH or
position_manager.STATE_FILE, so any test writing through the real
modules would land in production data (the phantom breakout row with
entry ~100.0 BTCUSDT is consistent with a fixture position leaking into
the live state.json and being closed at market by the running bot).

tests/conftest.py now redirects every known write-path module global to
tmp_path for EVERY test, autouse. These tests prove the guard is live.

Run: python -m pytest tests/test_hermetic_suite.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))


def test_journal_db_redirected_to_tmp():
    import journal
    from config import BOT_DIR as REAL_BOT_DIR
    assert Path(journal.DB_PATH) != (REAL_BOT_DIR / "trades.db"), (
        "journal.DB_PATH points at the PRODUCTION trades.db during tests")


def test_state_file_redirected_to_tmp():
    import position_manager
    from config import BOT_DIR as REAL_BOT_DIR
    assert Path(position_manager.STATE_FILE) != (REAL_BOT_DIR / "state.json"), (
        "position_manager.STATE_FILE points at the PRODUCTION state.json")


def test_control_flags_redirected_to_tmp():
    import control_flags
    from config import BOT_DIR as REAL_BOT_DIR
    assert Path(control_flags._FLAGS_PATH).parent != REAL_BOT_DIR


def test_routine_stamps_redirected_to_tmp():
    import routine_stamps
    from config import BOT_DIR as REAL_BOT_DIR
    assert Path(routine_stamps._STAMPS_PATH).parent != REAL_BOT_DIR


def test_log_trade_lands_in_redirected_db():
    """An actual write through the real log_trade API must land in the
    per-test temp DB and be readable back — full round trip, zero
    production contact."""
    import journal
    ok = journal.log_trade(
        symbol="HERMETICUSDT", direction="LONG",
        entry_price=1.0, exit_price=2.0, quantity=1.0,
        strategy="Hermetic Test", exit_reason="test",
    )
    assert ok is True
    assert Path(journal.DB_PATH).exists()
    rows = journal.read_trades(max_rows=50)
    assert any(t.get("symbol") == "HERMETICUSDT" for t in rows)


def test_save_state_lands_in_redirected_file():
    import position_manager as pm
    state = pm.load_state()
    state.setdefault("positions", {})
    pm.save_state(state, owner="momentum")
    assert Path(pm.STATE_FILE).exists()
