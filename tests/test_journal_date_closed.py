"""Aug 14 2026 — momentum never stamped date_closed.

Every bot's close path passes date_closed=datetime.now(timezone.utc) to
log_trade. main.py has three close call sites (partial TP1, full exit,
rotation) and NONE of them did. Momentum is the oldest bot in the fleet
and predates the convention every later bot followed.

Consequence: journal.read_trades_since filters on
`date_closed IS NOT NULL AND date_closed >= ?`, so every windowed query
— fleet_review --days 30, the dashboard's 30d panels, every day-N gate —
reported momentum as n=0. The Aug 14 review read "momentum: zero closed
trades in 30 days" when momentum had in fact been closing trades the
whole time. result/net_pnl derive from exit_price, so unwindowed totals
were right, which is exactly why this survived so long.

Run: python -m pytest tests/test_journal_date_closed.py -v
"""

from __future__ import annotations

import ast
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pytest.importorskip("pandas")


# ─── Static guard: every close call site stamps the timestamp ────────────

_CLOSE_PATH_MODULES = [
    "main.py", "breakout_main.py", "whale_main.py", "scalp_main.py",
    "crossover_main.py", "funding_main.py", "pair_main.py",
    "stock_daily_main.py",
]


def _log_trade_calls(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "log_trade"):
            yield node


def _is_close_call(call) -> bool:
    """An ENTRY row legitimately has no close time, and entry paths pass
    exit_price=None explicitly. A close is a call carrying an
    exit_reason, or a non-None exit_price."""
    kw = {k.arg: k.value for k in call.keywords}
    if "exit_reason" in kw:
        return True
    ep = kw.get("exit_price")
    return ep is not None and not (isinstance(ep, ast.Constant)
                                     and ep.value is None)


@pytest.mark.parametrize("module", _CLOSE_PATH_MODULES)
def test_every_log_trade_call_stamps_date_closed_or_is_an_open(module):
    """An entry row legitimately has no close time. A row written from a
    close path without one is invisible to every windowed query."""
    path = BOT_DIR / module
    if not path.exists():
        pytest.skip(f"{module} not present")
    for call in _log_trade_calls(path):
        kw = {k.arg for k in call.keywords}
        if _is_close_call(call) and "date_closed" not in kw:
            pytest.fail(
                f"{module}:{call.lineno} logs an exit without date_closed — "
                f"this row will not appear in any --days N query")


# ─── Behavioural: the window query must see a closed momentum trade ──────

def test_windowed_read_sees_a_trade_closed_today(tmp_path, monkeypatch):
    import journal
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "t.db")
    journal._INITIALIZED = False if hasattr(journal, "_INITIALIZED") else None
    now = datetime.now(timezone.utc)
    journal.log_trade(
        symbol="AAVEUSDT", direction="LONG", entry_price=100.0,
        exit_price=95.0, quantity=1.0, leverage=10,
        strategy="AAVE 4h Momentum", exit_reason="SL Hit",
        date_closed=now)
    rows = journal.read_recent_trades(hours=24 * 30)
    assert len(rows) == 1, "a trade closed today is missing from a 30d window"
    assert rows[0]["symbol"] == "AAVEUSDT"


def test_a_close_without_the_stamp_is_invisible_to_the_window(tmp_path,
                                                                monkeypatch):
    """Pins the failure mode itself so the regression is legible."""
    import journal
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "t2.db")
    now = datetime.now(timezone.utc)
    journal.log_trade(
        symbol="AAVEUSDT", direction="LONG", entry_price=100.0,
        exit_price=95.0, quantity=1.0, leverage=10,
        strategy="AAVE 4h Momentum", exit_reason="SL Hit")   # no date_closed
    assert journal.read_recent_trades(hours=24 * 30) == []
    # ...while the unwindowed read still shows it as a LOSS, which is
    # precisely why the totals looked right and the windows did not.
    all_rows = journal.read_trades(max_rows=10)
    assert all_rows[0]["result"] == "LOSS"
