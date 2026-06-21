"""Whale U.1 / U.2 / U.3 wire-up tests.

Three unblocks for the dormant whale bot:
- U.1 _fetch_1d_context: pull 1D klines, compute EMA20/50 + ATR/ADX,
  return DataFrame ready for multi-TF gate + regime classifier.
- U.2 same helper also returns the L.2 regime label.
- U.3 fetch_cohorts respects MIN_ACCOUNT_VALUE_USD + REQUIRE_POSITIVE_MONTH_PNL
  gates and sorts by a composite (pnl_month + scaled pnl_alltime) score,
  not raw all-time PnL — addresses the Phase W "lucky 3-month winner"
  survivorship bias.

Run: python -m pytest tests/test_whale_unblocks.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


# ─── U.1 / U.2 — _fetch_1d_context helper in whale_main ────────────────────

def _synthetic_1d_klines(n_bars: int = 250, base: float = 50_000.0,
                            drift: float = 0.001):
    """Build a fake klines array in WEEX raw shape (11 columns).

    signals.build_dataframe unconditionally accesses df["close_time"], so
    the row must have at least 7 elements. We pad to the full 11-column
    WEEX shape for parity with real responses.
    """
    rows = []
    for i in range(n_bars):
        ts = 1_700_000_000_000 + i * 86_400_000  # 1d cadence
        close = base * (1 + drift) ** i
        open_ = close * 0.999
        high = close * 1.005
        low = close * 0.995
        close_time = ts + 86_400_000 - 1
        rows.append([
            ts, str(open_), str(high), str(low), str(close), "1000",
            close_time, "1000000", 50, "500", "500000",
        ])
    return rows


def test_fetch_1d_context_returns_indicators_and_regime():
    """Happy path: 250 bars of mild uptrend → df has EMAs + ATR + ADX,
    regime_label is one of the 6 L.2 buckets."""
    from whale_main import _fetch_1d_context
    executor = MagicMock()
    executor.get_klines.return_value = _synthetic_1d_klines(250, drift=0.002)

    df_1d, regime_label = _fetch_1d_context(executor, "BTC_USDT_PERP")
    assert df_1d is not None
    # required columns the filters use
    assert "ema_fast" in df_1d.columns
    assert "ema_slow" in df_1d.columns
    # regime_label is non-null and one of the known L.2 labels
    assert regime_label is not None
    assert regime_label in {
        "strong_up", "weak_up", "strong_down", "weak_down",
        "range_high_vol", "range_low_vol", "unknown",
    }


def test_fetch_1d_context_returns_none_on_empty_klines():
    """Executor returned empty → graceful None,None so filters degrade to pass."""
    from whale_main import _fetch_1d_context
    executor = MagicMock()
    executor.get_klines.return_value = []
    df_1d, regime_label = _fetch_1d_context(executor, "FAKE_PERP")
    assert df_1d is None
    assert regime_label is None


def test_fetch_1d_context_returns_none_on_executor_exception():
    """Network failure → None, None (don't raise into the cycle)."""
    from whale_main import _fetch_1d_context
    executor = MagicMock()
    executor.get_klines.side_effect = RuntimeError("network down")
    df_1d, regime_label = _fetch_1d_context(executor, "BTC_USDT_PERP")
    assert df_1d is None
    assert regime_label is None


def test_fetch_1d_context_returns_partial_when_under_200_bars():
    """Fewer than 200 bars (regime needs 200 for ema200) — df returned with
    EMAs computed but regime_label may be 'unknown'. Filter still benefits."""
    from whale_main import _fetch_1d_context
    executor = MagicMock()
    executor.get_klines.return_value = _synthetic_1d_klines(150, drift=0.001)
    df_1d, regime_label = _fetch_1d_context(executor, "BTC_USDT_PERP")
    # Multi-TF filter still works because ema_fast/ema_slow only need ~50 bars
    assert df_1d is not None
    assert "ema_fast" in df_1d.columns
    # Regime may be unknown — that's fine, the gate degrades to pass
    # (we just need the call not to raise)


# ─── U.3 — cohort filter gates + composite sort ────────────────────────────

def test_cohort_filter_drops_below_min_account_value():
    """MIN_ACCOUNT_VALUE_USD gate — small accounts shouldn't be in 'smart' cohort."""
    from whale_signals import _qualifying_wallets, MIN_ACCOUNT_VALUE_USD
    parsed = [
        {"address": "0x1", "display_name": "A",
         "account_value": MIN_ACCOUNT_VALUE_USD - 1,
         "pnl_alltime": 500_000, "pnl_month": 50_000},
        {"address": "0x2", "display_name": "B",
         "account_value": MIN_ACCOUNT_VALUE_USD + 1,
         "pnl_alltime": 500_000, "pnl_month": 50_000},
    ]
    out = _qualifying_wallets(parsed, require_positive_month=False)
    addresses = {w["address"] for w in out}
    assert "0x1" not in addresses
    assert "0x2" in addresses


def test_cohort_filter_drops_negative_month_pnl_when_required():
    """REQUIRE_POSITIVE_MONTH_PNL — the bias kill: a 'top smart' wallet
    whose recent month is negative is the 'lucky 3-month winner' from
    Phase W's diagnosis."""
    from whale_signals import _qualifying_wallets, MIN_ACCOUNT_VALUE_USD
    parsed = [
        {"address": "0x1", "display_name": "currently bleeding",
         "account_value": MIN_ACCOUNT_VALUE_USD * 2,
         "pnl_alltime": 2_000_000, "pnl_month": -100_000},
        {"address": "0x2", "display_name": "still winning",
         "account_value": MIN_ACCOUNT_VALUE_USD * 2,
         "pnl_alltime": 2_000_000, "pnl_month": 25_000},
    ]
    out = _qualifying_wallets(parsed, require_positive_month=True)
    addresses = {w["address"] for w in out}
    assert "0x1" not in addresses
    assert "0x2" in addresses


def test_cohort_filter_allows_negative_month_when_not_required():
    """When the toggle is off, we keep the legacy behaviour (warning: still bias-prone)."""
    from whale_signals import _qualifying_wallets, MIN_ACCOUNT_VALUE_USD
    parsed = [
        {"address": "0x1", "display_name": "bleeding",
         "account_value": MIN_ACCOUNT_VALUE_USD * 2,
         "pnl_alltime": 2_000_000, "pnl_month": -100_000},
    ]
    out = _qualifying_wallets(parsed, require_positive_month=False)
    assert len(out) == 1


def test_cohort_composite_score_recent_beats_alltime():
    """Recent perf weighted heavier than all-time — addresses survivorship bias.
    A wallet with strong recent month should rank ABOVE a wallet with bigger
    all-time but flat month."""
    from whale_signals import _composite_score
    spike_wallet = {"pnl_alltime": 10_000_000, "pnl_month": 5_000}
    fresh_wallet = {"pnl_alltime": 500_000,   "pnl_month": 200_000}
    assert _composite_score(fresh_wallet) > _composite_score(spike_wallet)


def test_cohort_composite_score_uses_alltime_as_tiebreaker():
    """When two wallets have identical month PnL, all-time breaks the tie."""
    from whale_signals import _composite_score
    veteran = {"pnl_alltime": 5_000_000, "pnl_month": 50_000}
    rookie  = {"pnl_alltime":   500_000, "pnl_month": 50_000}
    assert _composite_score(veteran) > _composite_score(rookie)
