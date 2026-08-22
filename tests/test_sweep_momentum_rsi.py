"""The RSI sweep must not flatter a loosened filter (Aug 22 2026)."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pytest

pytest.importorskip("pandas")

from tools.sweep_momentum_rsi import ARMS, arm_config, compare, summarize


def _row(n=60, wr=45.0, pf=1.5, dd=20.0, ret=50.0):
    return {"n": n, "wr": wr, "pf": pf, "dd": dd, "ret": ret, "warnings": []}


# ─── Arm isolation ───────────────────────────────────────────────────────

def test_arm_config_only_changes_the_mode():
    base = {"rsi_min": 40, "rsi_max": 70, "sl_atr_mult": 1.0}
    out = arm_config(base, "range")
    assert out["rsi_mode"] == "range"
    assert {k: v for k, v in out.items() if k != "rsi_mode"} == base


def test_arm_config_does_not_mutate_the_original():
    """Sharing one dict across arms is how a sweep measures the same
    thing three times and reports it as agreement."""
    base = {"rsi_min": 40, "rsi_max": 70}
    arm_config(base, "off")
    assert "rsi_mode" not in base


def test_all_three_arms_are_covered():
    assert set(ARMS) == {"crossover", "range", "off"}


# ─── The verdict refuses small samples ───────────────────────────────────

def test_a_thin_baseline_yields_no_verdict():
    rows = {"crossover": _row(n=4), "off": _row(n=9, pf=9.0)}
    assert compare(rows).startswith("INSUFFICIENT")


def test_a_thin_challenger_cannot_win():
    rows = {"crossover": _row(n=60, pf=1.5), "range": _row(n=5, pf=9.0)}
    assert compare(rows) == "KEEP crossover"


# ─── PF alone is not enough ──────────────────────────────────────────────

def test_a_marginal_pf_lift_does_not_win():
    """+0.05 is noise on a 60-trade sample."""
    rows = {"crossover": _row(pf=1.50), "range": _row(pf=1.55)}
    assert compare(rows) == "KEEP crossover"


def test_a_pf_lift_bought_with_drawdown_does_not_win():
    """The failure mode this guard exists for: loosening a gate lifts PF
    while the equity curve gets far worse."""
    rows = {"crossover": _row(pf=1.50, dd=20.0),
            "off": _row(pf=2.00, dd=40.0)}
    assert compare(rows) == "KEEP crossover"


def test_a_real_lift_at_similar_drawdown_is_a_candidate():
    rows = {"crossover": _row(pf=1.50, dd=20.0),
            "range": _row(pf=1.90, dd=21.0)}
    assert compare(rows).startswith("CANDIDATE range")


def test_the_verdict_is_never_an_instruction_to_deploy():
    rows = {"crossover": _row(pf=1.50), "range": _row(pf=1.90)}
    assert "CANDIDATE" in compare(rows)
    assert "deploy" not in compare(rows).lower()


# ─── Summaries degrade honestly ──────────────────────────────────────────

def test_a_missing_report_summarizes_to_zero_with_a_warning():
    s = summarize(None)
    assert s["n"] == 0 and s["warnings"]


def test_warnings_are_carried_through():
    class _R:
        n_trades = 10
        win_rate = 40.0
        profit_factor = 1.1
        max_drawdown_pct = 5.0
        total_return_pct = 3.0
        gross_profit = 33.0
        gross_loss = 30.0
        warnings = ["TRUNCATED: 400 of 5000 bars"]
    s = summarize(_R())
    assert s["warnings"] == ["TRUNCATED: 400 of 5000 bars"]
    # gp/gl are carried so pooling sums real profits rather than
    # back-solving them out of n/wr/pf/ret.
    assert s["gp"] == 33.0 and s["gl"] == 30.0


# ─── Pooled comparison ───────────────────────────────────────────────────
#
# The per-asset run returned INSUFFICIENT on all ten assets: no baseline
# reached 20 trades in 5000 bars. Pooling gives enough sample to compare.
# These criteria were fixed BEFORE the pooled numbers were seen, which is
# the only thing that makes them criteria rather than a rationalisation.

from tools.sweep_momentum_rsi import (          # noqa: E402
    POOLED_MIN_N, compare_pooled, pool_arm)


def _asset(n=14, wr=71.4, pf=2.76, dd=17.3, gp=100.0, gl=36.0):
    return {"n": n, "wr": wr, "pf": pf, "dd": dd, "ret": 42.7,
            "gp": gp, "gl": gl, "warnings": []}


def test_pooled_pf_is_a_ratio_of_sums_not_an_average_of_ratios():
    """A 3-trade asset with a lucky PF must not weigh the same as a
    40-trade one — that is how a thin asset hijacks a fleet conclusion."""
    thin = _asset(n=3, gp=90.0, gl=1.0)      # PF 90 on 3 trades
    fat = _asset(n=40, gp=100.0, gl=100.0)   # PF 1.0 on 40 trades
    pooled = pool_arm([thin, fat])
    assert pooled["pf"] == pytest.approx(190.0 / 101.0, abs=0.01)
    assert pooled["pf"] < 5.0, "the 3-trade asset dominated the pool"


def test_pooling_sums_trade_counts():
    assert pool_arm([_asset(n=14), _asset(n=35)])["n"] == 49


def test_drawdown_is_reported_as_mean_and_max_never_pooled():
    """Each asset has its own equity curve. A single 'pooled drawdown'
    would describe a portfolio that never existed."""
    p = pool_arm([_asset(dd=10.0), _asset(dd=40.0)])
    assert p["dd_mean"] == pytest.approx(25.0)
    assert p["dd_max"] == pytest.approx(40.0)
    assert "dd" not in p


def test_empty_and_zero_trade_assets_are_skipped():
    assert pool_arm([])["n"] == 0
    assert pool_arm([_asset(n=0), None])["n"] == 0


# ─── The pre-registered verdict ──────────────────────────────────────────

def _pooled(pf, dd_mean=20.0, dd_max=30.0, n=140):
    return {"n": n, "wr": 50.0, "pf": pf, "dd_mean": dd_mean,
            "dd_max": dd_max, "assets": 10}


def test_a_thin_pool_still_refuses():
    out = compare_pooled({"crossover": _pooled(1.5, n=POOLED_MIN_N - 1)}, {})
    assert out.startswith("INSUFFICIENT")


def test_a_small_pf_lift_is_rejected_with_its_reason():
    pooled = {"crossover": _pooled(1.50), "range": _pooled(1.60)}
    out = compare_pooled(pooled, {})
    assert "KEEP crossover" in out and "PF lift" in out


def test_a_pf_lift_bought_with_drawdown_is_rejected():
    pooled = {"crossover": _pooled(1.50, dd_mean=20.0, dd_max=30.0),
              "off": _pooled(2.00, dd_mean=30.0, dd_max=50.0)}
    out = compare_pooled(pooled, {})
    assert "KEEP crossover" in out
    assert "mean DD" in out and "max DD" in out


def test_an_arm_that_loses_on_most_assets_is_rejected():
    """Pooled PF can be carried by one asset while the arm is worse
    nearly everywhere else."""
    pooled = {"crossover": _pooled(1.50), "range": _pooled(2.00)}
    per_asset = {"crossover": {f"A{i}": 2.0 for i in range(10)},
                 "range": {f"A{i}": 1.0 for i in range(10)}}
    out = compare_pooled(pooled, per_asset)
    assert "worse on 10 assets" in out


def test_a_clean_win_is_reported_as_a_candidate():
    pooled = {"crossover": _pooled(1.50), "range": _pooled(1.90)}
    per_asset = {"crossover": {f"A{i}": 1.0 for i in range(10)},
                 "range": {f"A{i}": 2.0 for i in range(10)}}
    out = compare_pooled(pooled, per_asset)
    assert out.startswith("CANDIDATE range")


def test_the_verdict_always_shows_its_reasoning():
    """A bare verdict invites trusting it; the reasons invite checking."""
    pooled = {"crossover": _pooled(1.50), "range": _pooled(1.55),
              "off": _pooled(1.40)}
    out = compare_pooled(pooled, {})
    assert "range:" in out and "off:" in out


# ─── Thin baseline vs. no information (Aug 22, from the pooled run) ──────
#
# The pooled run returned crossover n=91 against a pre-registered bar of
# 100 — nine trades short. The challengers came in at n=252 and n=341,
# both worse on PF and on drawdown.
#
# The threshold was NOT lowered to get a verdict. It exists to stop a
# challenger being adopted on thin evidence, and nothing is adopted
# here. What changed is that the report now distinguishes "cannot
# promote" from "no information", because evidence AGAINST a challenger
# is a different claim from evidence FOR the incumbent.

def test_a_thin_baseline_still_refuses_to_promote():
    pooled = {"crossover": _pooled(2.42, dd_mean=6.1, n=91),
              "range": _pooled(1.28, dd_mean=17.1, n=252)}
    assert compare_pooled(pooled, {}).startswith("INSUFFICIENT to promote")


def test_a_thin_baseline_still_reports_a_beaten_challenger():
    """The real pooled numbers: n=91 baseline, well-sampled worse arms."""
    pooled = {"crossover": _pooled(2.42, dd_mean=6.1, dd_max=17.3, n=91),
              "range": _pooled(1.28, dd_mean=17.1, dd_max=38.9, n=252),
              "off": _pooled(1.18, dd_mean=20.3, dd_max=40.8, n=341)}
    out = compare_pooled(pooled, {})
    assert "NO CASE TO LOOSEN" in out
    assert "range" in out and "off" in out


def test_a_challenger_is_only_reported_beaten_on_BOTH_axes():
    """A challenger with a lower PF but BETTER drawdown is a real
    trade-off, not a defeat. Calling it beaten would be the same
    single-axis reading this tool exists to prevent."""
    pooled = {"crossover": _pooled(2.42, dd_mean=20.0, n=91),
              "range": _pooled(1.28, dd_mean=5.0, n=252)}
    assert "NO CASE TO LOOSEN" not in compare_pooled(pooled, {})


def test_a_thin_challenger_is_not_reported_as_beaten():
    """Beating a challenger requires the challenger to be measured."""
    pooled = {"crossover": _pooled(2.42, dd_mean=6.1, n=91),
              "range": _pooled(1.28, dd_mean=17.1, n=30)}
    assert "NO CASE TO LOOSEN" not in compare_pooled(pooled, {})


def test_nothing_is_ever_promoted_from_a_thin_baseline():
    """The bar holds in the direction that matters: no challenger can
    be adopted, however good it looks, when the baseline is thin."""
    pooled = {"crossover": _pooled(1.00, dd_mean=30.0, n=91),
              "range": _pooled(9.00, dd_mean=1.0, n=500)}
    out = compare_pooled(pooled, {})
    assert "CANDIDATE" not in out
