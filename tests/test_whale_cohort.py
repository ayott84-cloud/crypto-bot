"""Phase W.C — cohort quality scoring tests.

Replaces the survivorship-biased 90d-PnL ranking with a composite score
that rewards consistency over raw PnL spikes:
  score = 0.5 * normalized_sharpe + 0.3 * normalized_pnl + 0.2 * normalized_longevity

Public functions:
  compute_wallet_score(wallet_data, normalizers)
  longevity_qualifies(days_active, min_days=180)
  filter_qualifying_wallets(wallets, min_score, min_days)

Run: python -m pytest tests/test_whale_cohort.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

import whale_cohort


# ─── Longevity filter ─────────────────────────────────────────────────────

def test_longevity_qualifies_below_threshold():
    assert whale_cohort.longevity_qualifies(days_active=100, min_days=180) is False


def test_longevity_qualifies_above_threshold():
    assert whale_cohort.longevity_qualifies(days_active=200, min_days=180) is True


def test_longevity_qualifies_handles_none_or_negative():
    assert whale_cohort.longevity_qualifies(days_active=None, min_days=180) is False
    assert whale_cohort.longevity_qualifies(days_active=-1, min_days=180) is False


# ─── Wallet score composition ────────────────────────────────────────────

def test_score_higher_for_high_sharpe():
    """Two wallets with same PnL — higher Sharpe should score higher."""
    high_sharpe = {"sharpe": 2.0, "pnl_90d_usd": 50_000, "days_active": 300}
    low_sharpe  = {"sharpe": 0.5, "pnl_90d_usd": 50_000, "days_active": 300}
    normalizers = {"max_sharpe": 2.0, "max_pnl": 100_000, "max_days": 365}
    s_hi = whale_cohort.compute_wallet_score(high_sharpe, normalizers)
    s_lo = whale_cohort.compute_wallet_score(low_sharpe, normalizers)
    assert s_hi > s_lo


def test_score_higher_for_longer_history():
    """Same Sharpe/PnL but more days_active should score higher."""
    new_wallet = {"sharpe": 1.0, "pnl_90d_usd": 30_000, "days_active": 90}
    old_wallet = {"sharpe": 1.0, "pnl_90d_usd": 30_000, "days_active": 365}
    normalizers = {"max_sharpe": 2.0, "max_pnl": 100_000, "max_days": 365}
    s_new = whale_cohort.compute_wallet_score(new_wallet, normalizers)
    s_old = whale_cohort.compute_wallet_score(old_wallet, normalizers)
    assert s_old > s_new


def test_score_is_zero_for_missing_sharpe():
    """No Sharpe data → contribute zero on that axis (don't crash)."""
    wallet = {"pnl_90d_usd": 50_000, "days_active": 200}
    normalizers = {"max_sharpe": 2.0, "max_pnl": 100_000, "max_days": 365}
    score = whale_cohort.compute_wallet_score(wallet, normalizers)
    assert score >= 0  # didn't crash, score is non-negative


def test_score_bounded_in_unit_interval_for_max_inputs():
    """Hitting all normalizer ceilings → score should be ~1.0 (the weighted sum)."""
    wallet = {"sharpe": 2.0, "pnl_90d_usd": 100_000, "days_active": 365}
    normalizers = {"max_sharpe": 2.0, "max_pnl": 100_000, "max_days": 365}
    score = whale_cohort.compute_wallet_score(wallet, normalizers)
    assert 0.99 <= score <= 1.01


# ─── filter_qualifying_wallets ────────────────────────────────────────────

def test_filter_keeps_wallets_meeting_both_score_and_longevity():
    wallets = [
        {"address": "0xA", "sharpe": 1.5, "pnl_90d_usd": 80_000, "days_active": 250},
        {"address": "0xB", "sharpe": 0.2, "pnl_90d_usd": 20_000, "days_active": 250},  # low Sharpe
        {"address": "0xC", "sharpe": 1.8, "pnl_90d_usd": 90_000, "days_active": 90},  # too new
    ]
    qualified = whale_cohort.filter_qualifying_wallets(
        wallets, min_score=0.4, min_days=180)
    addrs = [w["address"] for w in qualified]
    assert "0xA" in addrs
    assert "0xB" not in addrs
    assert "0xC" not in addrs


def test_filter_handles_empty_list():
    assert whale_cohort.filter_qualifying_wallets([], min_score=0.5, min_days=180) == []
