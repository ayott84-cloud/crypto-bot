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
        warnings = ["TRUNCATED: 400 of 5000 bars"]
    assert summarize(_R())["warnings"] == ["TRUNCATED: 400 of 5000 bars"]
