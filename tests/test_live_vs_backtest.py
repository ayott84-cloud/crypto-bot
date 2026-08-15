"""Small-sample discipline (Aug 14 2026).

Breakout showed WR 10% over 10 trades and ETHUSDT went 0-for-5, which
reads as a broken asset. ETH_4H's honest evidence is PF 1.87 over 107
trades and 7.8 years, and a 42%-win-rate strategy loses five straight
about 6.5% of the time. Parking on that would be the small-sample
reaction the project exists to avoid — scalp was parked on a 2.85-YEAR
window, not a five-trade one.

The distinction this module is really for: UNDERPOWERED is not
CONSISTENT. At n=5 against a 42% rate, even zero wins lands at 6.5%, so
nothing observable in that window could have rejected the strategy. One
of those is evidence the strategy is fine; the other is no evidence at
all, and collapsing them lets a dashboard imply a verdict it never
earned.

Run: python -m pytest tests/test_live_vs_backtest.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pytest

from tools.live_vs_backtest import (
    binomial_tail_le, min_trades_to_reject, verdict)


# ─── The tail ────────────────────────────────────────────────────────────

def test_zero_wins_is_just_the_all_loss_probability():
    assert binomial_tail_le(0, 5, 0.421) == pytest.approx(0.579 ** 5, rel=1e-6)


def test_every_outcome_included_is_certainty():
    assert binomial_tail_le(7, 7, 0.4) == pytest.approx(1.0)


def test_empty_sample_cannot_indict():
    assert binomial_tail_le(0, 0, 0.4) == 1.0


def test_the_observed_eth_streak():
    """0-for-5 against ETH_4H's validated 42.1%."""
    assert binomial_tail_le(0, 5, 0.421) == pytest.approx(0.0651, abs=5e-4)


def test_the_observed_breakout_window():
    """1 win in 10 against a ~37% blended rate."""
    assert binomial_tail_le(1, 10, 0.37) == pytest.approx(0.0677, abs=5e-4)


# ─── Power ───────────────────────────────────────────────────────────────

def test_a_five_trade_window_cannot_reject_a_42pct_strategy():
    assert min_trades_to_reject(0.421) > 5


def test_min_trades_is_the_point_where_zero_wins_would_clear_the_bar():
    n = min_trades_to_reject(0.421)
    assert binomial_tail_le(0, n, 0.421) <= 0.05
    assert binomial_tail_le(0, n - 1, 0.421) > 0.05


def test_a_higher_win_rate_needs_fewer_trades_to_indict():
    assert min_trades_to_reject(0.70) < min_trades_to_reject(0.30)


# ─── Verdicts ────────────────────────────────────────────────────────────

def test_eth_zero_for_five_is_underpowered_not_divergent():
    """The finding: there is no case to park ETH on this window."""
    v = verdict(wins=0, n=5, expected_wr_pct=42.1)
    assert v["verdict"] == "UNDERPOWERED"
    assert v["min_n"] > 5


def test_underpowered_is_never_reported_as_consistent():
    """They read identically on a dashboard and mean opposite things."""
    assert verdict(0, 3, 42.1)["verdict"] != "CONSISTENT"


def test_a_long_bad_run_is_divergent():
    v = verdict(wins=1, n=40, expected_wr_pct=42.1)
    assert v["verdict"] == "DIVERGENT"
    assert v["p_value"] < 0.05


def test_a_result_near_the_validated_rate_is_consistent():
    v = verdict(wins=17, n=40, expected_wr_pct=42.1)
    assert v["verdict"] == "CONSISTENT"


def test_outperformance_is_never_flagged():
    """One-sided by design — beating the backtest needs no defence."""
    assert verdict(wins=35, n=40, expected_wr_pct=42.1)["verdict"] \
        == "CONSISTENT"


def test_no_trades_reports_no_data_rather_than_a_verdict():
    v = verdict(wins=0, n=0, expected_wr_pct=42.1)
    assert v["verdict"] == "NO DATA"


def test_live_and_expected_rates_are_both_reported():
    v = verdict(wins=2, n=10, expected_wr_pct=42.1)
    assert v["live_wr"] == pytest.approx(20.0)
    assert v["expected_wr"] == pytest.approx(42.1)


# ─── The scalp precedent must still fail ─────────────────────────────────

def test_the_window_that_parked_scalp_still_indicts():
    """Scalp was parked on a 2.85-year replay. A tool that lets that
    result pass would be useless — this is the guard against making the
    check so conservative that nothing is ever actionable."""
    v = verdict(wins=6, n=60, expected_wr_pct=40.0)
    assert v["verdict"] == "DIVERGENT"
