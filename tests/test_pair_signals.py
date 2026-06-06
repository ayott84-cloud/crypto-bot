"""Phase F.1 — pair-trade signal tests (ETH/BTC z-score reversion).

Per plan F:
  - Ratio = ETH_close / BTC_close, computed each bar
  - Rolling z-score over `window` bars (default 30 days at 1d timeframe)
  - Entry LONG_ETH_SHORT_BTC when z <= -2 (ETH undervalued vs BTC)
    Entry SHORT_ETH_LONG_BTC when z >= +2 (ETH overvalued vs BTC)
  - Exit when |z| <= 0.5 (reversion to mean)
  - Hard exit: 5 bars elapsed, or spread moves 2 ATR-of-spread against

Run: python -m pytest tests/test_pair_signals.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

import pair_signals


# ─── Fixtures ──────────────────────────────────────────────────────────────

def _cfg(**overrides):
    base = {
        "z_window":          30,
        "entry_z":           2.0,
        "exit_z":            0.5,
        "max_hold_bars":     5,
        "atr_stop_mult":     2.0,
        "atr_window":        14,
    }
    base.update(overrides)
    return base


def _pair_df(ratios):
    """Build a single-column ratio DataFrame for z-score testing."""
    return pd.DataFrame({"ratio": ratios})


# ─── compute_ratio ────────────────────────────────────────────────────────

def test_compute_ratio_divides_eth_by_btc():
    eth = pd.Series([2000, 2100, 2050])
    btc = pd.Series([40000, 42000, 41000])
    r = pair_signals.compute_ratio(eth, btc)
    assert r.iloc[0] == pytest.approx(0.05)
    assert r.iloc[1] == pytest.approx(0.05)
    assert r.iloc[2] == pytest.approx(2050 / 41000)


def test_compute_ratio_handles_zero_btc_safely():
    """Division by zero shouldn't crash — return NaN for that bar."""
    eth = pd.Series([2000, 2100])
    btc = pd.Series([0, 42000])
    r = pair_signals.compute_ratio(eth, btc)
    assert pd.isna(r.iloc[0])
    assert r.iloc[1] == pytest.approx(0.05)


# ─── z-score ──────────────────────────────────────────────────────────────

def test_z_score_returns_zero_when_constant_ratio():
    """No variance → z-score is 0 (or NaN; we return 0 for stability)."""
    ratios = [0.05] * 35
    z = pair_signals.rolling_z_score(_pair_df(ratios)["ratio"], window=30)
    assert z.iloc[-1] == pytest.approx(0.0, abs=0.01)


def test_z_score_positive_when_recent_above_mean():
    """A run of high ratios → positive z."""
    ratios = [0.04] * 30 + [0.06] * 5  # 35 bars; last 5 way above mean
    z = pair_signals.rolling_z_score(_pair_df(ratios)["ratio"], window=30)
    assert z.iloc[-1] > 1.0


def test_z_score_negative_when_recent_below_mean():
    ratios = [0.06] * 30 + [0.04] * 5
    z = pair_signals.rolling_z_score(_pair_df(ratios)["ratio"], window=30)
    assert z.iloc[-1] < -1.0


def test_z_score_nan_for_first_window_minus_one_bars():
    """A rolling window needs `window` bars to produce a value."""
    ratios = list(range(35))
    z = pair_signals.rolling_z_score(_pair_df(ratios)["ratio"], window=30)
    assert pd.isna(z.iloc[0])
    assert not pd.isna(z.iloc[-1])


# ─── analyze_pair_entry ───────────────────────────────────────────────────

def test_pair_entry_long_eth_short_btc_when_z_below_negative_threshold():
    """z <= -2: ETH undervalued vs BTC → buy ETH, short BTC."""
    eth = pd.Series([2000] * 30 + [1800])
    btc = pd.Series([40000] * 31)
    sig = pair_signals.analyze_pair_entry(eth, btc, _cfg())
    assert sig["would_enter"] is True
    assert sig["direction"] == "LONG_ETH_SHORT_BTC"
    assert sig["z"] < -1.99


def test_pair_entry_short_eth_long_btc_when_z_above_positive_threshold():
    """z >= +2: ETH overvalued vs BTC → short ETH, buy BTC."""
    eth = pd.Series([2000] * 30 + [2300])
    btc = pd.Series([40000] * 31)
    sig = pair_signals.analyze_pair_entry(eth, btc, _cfg())
    assert sig["would_enter"] is True
    assert sig["direction"] == "SHORT_ETH_LONG_BTC"


def test_pair_entry_blocked_when_z_within_band():
    """|z| < entry_z → no entry. Uses a realistic noisy series so the
    rolling-std isn't degenerate."""
    import numpy as np
    rng = np.random.default_rng(42)
    # 30 bars with real variance ±5%, last bar near the mean → z ~0
    base = 2000 * (1 + rng.normal(0, 0.05, 30))
    eth = pd.Series(list(base) + [float(base.mean())])
    btc = pd.Series([40000] * 31)
    sig = pair_signals.analyze_pair_entry(eth, btc, _cfg())
    assert sig["would_enter"] is False
    assert sig["blocked_by"] == "within_band"


def test_pair_entry_blocked_when_insufficient_history():
    eth = pd.Series([2000] * 10)
    btc = pd.Series([40000] * 10)
    sig = pair_signals.analyze_pair_entry(eth, btc, _cfg(z_window=30))
    assert sig["would_enter"] is False
    assert sig["blocked_by"] == "insufficient_data"


# ─── check_pair_exit ──────────────────────────────────────────────────────

def _noisy_series(mean, std_frac, n, seed, last_offset_sigmas=0.0):
    """Generate a noisy price series whose last bar sits N sigmas from mean.

    Returns a list of length n. Used to construct z-scores at known
    target values without depending on degenerate fixtures.
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    prior = mean * (1 + rng.normal(0, std_frac, n - 1))
    # Compute the prior std so we can set the last bar to target z
    prior_std = float(np.std(prior, ddof=1))
    last = float(np.mean(prior)) + last_offset_sigmas * prior_std
    return list(prior) + [last]


def test_pair_exit_when_z_returns_within_exit_band():
    """|z| <= exit_z (0.5) → reversion done, close both legs."""
    eth = pd.Series(_noisy_series(2000, 0.03, 31, seed=1, last_offset_sigmas=0.2))
    btc = pd.Series([40000] * 31)
    reason, kind = pair_signals.check_pair_exit(
        eth, btc, position_direction="LONG_ETH_SHORT_BTC",
        bars_held=2, entry_ratio=0.045, cfg=_cfg())
    assert reason == "Z Reverted"
    assert kind == "full"


def test_pair_exit_when_max_hold_reached():
    """Even if z is still outside exit band, close after max_hold_bars."""
    # z around -2 (outside exit band but not past stop), bars_held > max
    eth = pd.Series(_noisy_series(2000, 0.03, 31, seed=2, last_offset_sigmas=-2.0))
    btc = pd.Series([40000] * 31)
    reason, kind = pair_signals.check_pair_exit(
        eth, btc, position_direction="LONG_ETH_SHORT_BTC",
        bars_held=6, entry_ratio=0.05, cfg=_cfg(max_hold_bars=5))
    assert reason == "Time Stop"
    assert kind == "full"


def test_pair_exit_when_spread_moves_against():
    """z moves AWAY from 0 past atr_stop_mult × entry_z → stop out.

    Need offset >> -4 because the rolling window includes the last bar,
    which dilutes the magnitude. Use -7 to land z below -4.
    """
    eth = pd.Series(_noisy_series(2000, 0.03, 31, seed=3, last_offset_sigmas=-7.0))
    btc = pd.Series([40000] * 31)
    reason, kind = pair_signals.check_pair_exit(
        eth, btc, position_direction="LONG_ETH_SHORT_BTC",
        bars_held=2, entry_ratio=0.045, cfg=_cfg(atr_stop_mult=2.0))
    assert reason == "Z Stop"


def test_pair_no_exit_when_z_outside_exit_band_within_time_no_stop():
    """z around -1.5 — outside exit band, time stop not hit, no Z Stop."""
    eth = pd.Series(_noisy_series(2000, 0.03, 31, seed=4, last_offset_sigmas=-1.5))
    btc = pd.Series([40000] * 31)
    reason, kind = pair_signals.check_pair_exit(
        eth, btc, position_direction="LONG_ETH_SHORT_BTC",
        bars_held=1, entry_ratio=0.045, cfg=_cfg())
    assert reason is None
    assert kind is None
