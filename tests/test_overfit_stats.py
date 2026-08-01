"""Module 2 Phase S2 — multiple-testing haircuts.

Two gates the crypto module never needed and this one cannot do without.

DEFLATED SHARPE RATIO (Bailey & Lopez de Prado). Run enough
specifications and the best one looks brilliant by construction. DSR
asks: given that I tried N specs, and given this return series' skew,
kurtosis and length, what is the probability the true Sharpe is above
zero? The S2 sleeve is an ENSEMBLE over lookbacks — dozens to hundreds
of trials — so an undeflated Sharpe there is not evidence.

PROBABILITY OF BACKTEST OVERFITTING (CSCV). Splits the trial matrix into
in-sample/out-of-sample halves every possible way and asks how often the
IS winner lands below median OOS. PBO ~ 0.5 means the selection carries
no information — the result the crypto module reached the expensive way,
by shipping a scalp bot whose 1-year window was a favorable slice.

Run: python -m pytest tests/test_overfit_stats.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

np = pytest.importorskip("numpy")

from tools import overfit_stats as ofs


# ─── Sharpe helper ────────────────────────────────────────────────────────

def test_sharpe_of_a_constant_positive_series_is_capped_not_infinite():
    assert ofs.sharpe([1.0] * 50) == pytest.approx(ofs.SHARPE_CAP)


def test_sharpe_sign_matches_the_mean():
    rng = np.random.default_rng(1)
    assert ofs.sharpe(list(rng.normal(0.5, 1.0, 500))) > 0
    assert ofs.sharpe(list(rng.normal(-0.5, 1.0, 500))) < 0


# ─── Deflated Sharpe ──────────────────────────────────────────────────────

def test_dsr_is_high_for_a_strong_single_trial():
    rng = np.random.default_rng(3)
    r = list(rng.normal(0.15, 1.0, 2000))          # genuinely positive
    out = ofs.deflated_sharpe(r, n_trials=1)
    assert out["dsr"] > 0.95
    assert out["passes"] is True


def test_dsr_falls_as_the_number_of_trials_rises():
    """The whole point: the SAME series is weaker evidence when it was
    the best of many."""
    rng = np.random.default_rng(5)
    r = list(rng.normal(0.06, 1.0, 1000))
    few = ofs.deflated_sharpe(r, n_trials=1)["dsr"]
    many = ofs.deflated_sharpe(r, n_trials=1000)["dsr"]
    assert many < few


def test_dsr_rejects_a_noise_winner_selected_from_many_trials():
    """Best-of-500 coin flips looks great undeflated and must not pass."""
    rng = np.random.default_rng(9)
    trials = [rng.normal(0.0, 1.0, 750) for _ in range(500)]
    best = max(trials, key=lambda x: ofs.sharpe(list(x)))
    out = ofs.deflated_sharpe(list(best), n_trials=500)
    assert ofs.sharpe(list(best)) > 0, "fixture should have a positive winner"
    assert out["passes"] is False, "deflation failed to kill a noise winner"


def test_dsr_accounts_for_negative_skew():
    """Two series, same Sharpe, one with a fat left tail: the skewed one
    must deflate harder. Crash-prone equity strategies live here."""
    rng = np.random.default_rng(13)
    base = rng.normal(0.10, 1.0, 1500)
    skewed = base.copy()
    skewed[::75] -= 6.0                     # occasional deep losses
    skewed = skewed * (base.std() / skewed.std())
    skewed = skewed - skewed.mean() + base.mean()
    a = ofs.deflated_sharpe(list(base), n_trials=20)
    b = ofs.deflated_sharpe(list(skewed), n_trials=20)
    assert b["skew"] < a["skew"]
    assert b["dsr"] <= a["dsr"] + 1e-9


def test_dsr_handles_degenerate_input():
    assert ofs.deflated_sharpe([], n_trials=5)["dsr"] == 0.0
    assert ofs.deflated_sharpe([1.0], n_trials=5)["dsr"] == 0.0


# ─── PBO / CSCV ───────────────────────────────────────────────────────────

def test_pbo_is_low_when_one_strategy_genuinely_dominates():
    rng = np.random.default_rng(17)
    T, N = 600, 12
    m = rng.normal(0.0, 1.0, (T, N))
    m[:, 0] += 0.45                          # a real, persistent edge
    out = ofs.pbo_cscv(m, n_splits=8)
    assert out["pbo"] < 0.25
    assert out["n_combinations"] == 70       # C(8,4)


def test_pbo_is_near_half_for_pure_noise():
    """No strategy has an edge, so picking the IS winner is a coin flip
    out of sample. This is the number that would have caught the scalp
    sleeve's favorable-window problem before it cost three weeks."""
    rng = np.random.default_rng(19)
    m = rng.normal(0.0, 1.0, (600, 16))
    out = ofs.pbo_cscv(m, n_splits=8)
    assert 0.3 < out["pbo"] < 0.7


def test_pbo_requires_an_even_split_count():
    rng = np.random.default_rng(23)
    with pytest.raises(ValueError):
        ofs.pbo_cscv(rng.normal(0, 1, (100, 5)), n_splits=7)


def test_pbo_needs_at_least_two_strategies():
    rng = np.random.default_rng(29)
    out = ofs.pbo_cscv(rng.normal(0, 1, (100, 1)), n_splits=4)
    assert out["pbo"] is None
    assert "at least 2" in out["note"]


def test_pbo_reports_oos_degradation():
    """Beyond the headline: how much of the IS edge survives OOS."""
    rng = np.random.default_rng(31)
    m = rng.normal(0.0, 1.0, (600, 10))
    m[:, 3] += 0.4
    out = ofs.pbo_cscv(m, n_splits=8)
    assert "median_oos_rank" in out
    assert 0.0 <= out["median_oos_rank"] <= 1.0
