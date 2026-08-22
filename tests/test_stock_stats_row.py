"""Aug 15 2026 — the S2 record lost a field to hand transcription.

STOCK_BACKTEST_STATS carries wr=0.0 for every equity sleeve. The value
was never missing from the run: BacktestReport.win_rate is computed and
correct. The validator's summary line simply never printed it, and the
config rows were typed by hand from that summary — so the field could
not be transcribed and nobody noticed until tools/live_vs_backtest.py
tried to compare a stock sleeve and had nothing to compare against.

The fix is not "remember to include the win rate". It is to emit the
whole dict, so the manual step where a field goes missing no longer
exists.

Run: python -m pytest tests/test_stock_stats_row.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pytest

pytest.importorskip("pandas")

from tools.validate_stock_sleeves import stats_row


class _Rep:
    def __init__(self, wr=63.5, n=562):
        self.win_rate = wr
        self.n_trades = n


def _parse(row: str) -> dict:
    """The emitted text must be valid JSON once the key is stripped."""
    assert row.startswith('"stats": ')
    return json.loads(row[len('"stats": '):])


# ─── Every consumed field is present ─────────────────────────────────────

@pytest.mark.parametrize("field", [
    "pf", "wr", "trades", "dd_pct", "sharpe", "dsr", "pbo", "years"])
def test_row_carries_every_field_the_gates_consume(field):
    row = _parse(stats_row(_Rep(), 0.71, 12.4, 1.64,
                             {"dsr": 0.99}, {"pbo": 0.02}, 25.0))
    assert field in row


def test_the_win_rate_is_the_one_that_went_missing():
    row = _parse(stats_row(_Rep(wr=63.5), 0.71, 12.4, 1.64,
                             {"dsr": 0.99}, {"pbo": 0.02}, 25.0))
    assert row["wr"] == pytest.approx(63.5)
    assert row["wr"] > 0, "a zero win rate is what broke the comparison"


def test_output_is_machine_readable():
    """Paste-ready means parseable, not merely printable — that is what
    takes the human out of the transcription."""
    row = _parse(stats_row(_Rep(), 0.71, 12.4, 1.64,
                             {"dsr": 0.99}, {"pbo": 0.02}, 25.0))
    assert row["trades"] == 562 and row["pf"] == pytest.approx(1.64)


# ─── Degradation ─────────────────────────────────────────────────────────

def test_missing_overfit_stats_do_not_break_the_row():
    """DSR/PBO are absent when a sleeve has too few specs to compute
    them. That must not cost the row its other fields."""
    row = _parse(stats_row(_Rep(), 0.71, 12.4, 1.64, None, None, 25.0))
    assert row["dsr"] == 0 and row["pbo"] == 0
    assert row["wr"] == pytest.approx(63.5)


def test_a_zero_trade_sleeve_still_emits_a_row():
    row = _parse(stats_row(_Rep(wr=0.0, n=0), 0.0, 0.0, 0.0, {}, {}, 25.0))
    assert row["trades"] == 0


# ─── The recurrence guard ────────────────────────────────────────────────

def test_recorded_stock_stats_are_flagged_when_a_win_rate_is_absent():
    """Documents the CURRENT state of the config rather than asserting a
    value I have not re-measured. When the S2 re-run lands, these rows
    gain a win rate and this test's message stops applying — it exists so
    the gap stays visible until then, not to freeze it."""
    from stock_config import STOCK_BACKTEST_STATS
    traded = {k: v for k, v in STOCK_BACKTEST_STATS.items()
               if (v or {}).get("trades")}
    assert traded, "no stock sleeve has recorded trades at all"
    missing = [k for k, v in traded.items() if not v.get("wr")]
    # Not an assertion failure: the re-run is an operator job on the
    # droplet. This keeps the list printable and the gap honest.
    if missing:
        print(f"\nsleeves still lacking a win rate: {sorted(missing)}")
