"""Shadow cost model — signal price vs actual fill (Aug 14 2026).

Alpaca paper matches against real NBBO but models no fees, slippage,
market impact or queue position, so it flatters every strategy by
roughly our whole cost model. This metric is the defence, and the S3
plan made it a gate rather than a diagnostic for that reason.

Run: python -m pytest tests/test_fill_divergence.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from tools.fill_divergence import divergence_rows, summarize, verdict


def _t(bot, pct, days_ago=1, key="date_closed"):
    return {"bot": bot, "fill_divergence_pct": pct,
            key: (datetime.now() - timedelta(days=days_ago)).isoformat()}


# ─── Windowing ───────────────────────────────────────────────────────────

def test_only_trades_inside_the_window_count():
    rows = divergence_rows([_t("StockRev", 0.9, days_ago=2),
                             _t("StockRev", 5.0, days_ago=90)], days=30)
    assert rows["StockRev"] == [0.9]


def test_open_trades_are_windowed_on_their_open_time():
    """An entry records the divergence; waiting for the close would hide
    a bad fill for as long as the position runs."""
    rows = divergence_rows([_t("StockRev", 0.4, key="date_opened")], days=30)
    assert rows["StockRev"] == [0.4]


def test_trades_without_the_field_are_skipped_not_zeroed():
    """Counting a missing measurement as 0.0 would report a perfect fill
    for every bot that never recorded one."""
    rows = divergence_rows([{"bot": "Momentum",
                              "date_closed": datetime.now().isoformat()}],
                             days=30)
    assert rows == {}


def test_a_malformed_value_does_not_take_down_the_report():
    rows = divergence_rows([_t("StockRev", "not-a-number"),
                             _t("StockRev", 0.2)], days=30)
    assert rows["StockRev"] == [0.2]


def test_direction_is_ignored_only_magnitude_matters():
    """A fill 1% BELOW the signal on a long is luck, not skill; both
    directions are execution noise and both belong in the estimate."""
    rows = divergence_rows([_t("StockRev", -0.8), _t("StockRev", 0.8)],
                            days=30)
    assert rows["StockRev"] == [0.8, 0.8]


# ─── Summary ─────────────────────────────────────────────────────────────

def test_summary_reports_median_mean_p90_and_worst():
    s = summarize([0.1, 0.2, 0.3, 0.4, 5.0])
    assert s["n"] == 5
    assert s["median"] == 0.3
    assert s["worst"] == 5.0
    assert s["mean"] > s["median"], "one bad fill should not set the headline"


def test_empty_input_reports_no_sample_rather_than_zero():
    assert summarize([]) == {"n": 0}


# ─── Verdict ─────────────────────────────────────────────────────────────

def test_a_tight_fill_passes():
    assert verdict(0.03) == "PASS"


def test_a_drifting_fill_is_watched():
    assert verdict(0.15) == "WATCH"


def test_the_observed_qqq_divergence_fails_outright():
    """OPEN QQQ @ 730.70 on a 723.70 signal = 0.97%."""
    assert verdict(0.97).startswith("FAIL")


def test_the_gate_boundary_is_inclusive():
    assert verdict(0.05) == "PASS"
