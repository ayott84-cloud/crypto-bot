"""P5 code-review fixes — regression tests for the 10 confirmed findings.

Each test class maps to one finding from the Jul 2026 P5a review of the
P1-P4 rebuild. RED first, then the fix.

Run: python -m pytest tests/test_p5_review_fixes.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


# ─── Finding 1 — funding parser must accept the live WEEX key ──────────────

def test_funding_parser_accepts_lastFundingRate():
    from breakout_main import _parse_funding_rate_8h
    raw = [{"symbol": "cmt_btcusdt", "lastFundingRate": "0.000225"}]
    assert _parse_funding_rate_8h(raw) == pytest.approx(0.000225)


def test_funding_parser_prefers_lastFundingRate_over_generic():
    from breakout_main import _parse_funding_rate_8h
    raw = [{"lastFundingRate": "0.0002", "rate": "9.9"}]
    assert _parse_funding_rate_8h(raw) == pytest.approx(0.0002)


# ─── Findings 2+3 — deploy migration: legacy positions keep legacy exits ───

def test_scalp_position_is_atr_bracket_flag():
    """Positions carry bracket_kind='atr' only when opened by M.3 code;
    legacy positions (no field) must route to the legacy pct exit."""
    from scalp_main import _position_uses_atr_bracket
    assert _position_uses_atr_bracket({"bracket_kind": "atr",
                                          "atr_at_entry": 12.0}) is True
    # pre-M.3 position: real range-proxy value but no bracket_kind
    assert _position_uses_atr_bracket({"atr_at_entry": 3.7}) is False
    assert _position_uses_atr_bracket({}) is False


def test_crossover_position_invalidation_flag():
    """Same migration guard for crossover: only positions opened by N.3
    code (exit_kind='invalidation') use the v3 exit; legacy positions
    keep the pct-bracket exit they were opened with."""
    from crossover_main import _position_uses_invalidation_exit
    assert _position_uses_invalidation_exit(
        {"exit_kind": "invalidation", "atr_at_entry": 12.0}) is True
    assert _position_uses_invalidation_exit({"atr_at_entry": 3.7}) is False
    assert _position_uses_invalidation_exit({}) is False


# ─── Finding 5 — daily-regime inputs pinned to COMPLETED daily bars ────────

def test_completed_daily_closes_trims_forming_bar():
    from regime import completed_daily_closes
    s = pd.Series([100.0, 101.0, 102.0, 103.0])
    out = completed_daily_closes(s, last_bar_forming=True)
    assert list(out) == [100.0, 101.0, 102.0]
    out2 = completed_daily_closes(s, last_bar_forming=False)
    assert list(out2) == [100.0, 101.0, 102.0, 103.0]
    assert completed_daily_closes(None, last_bar_forming=True) is None


def test_replay_daily_slice_excludes_current_day():
    """Replay must only see FULLY COMPLETED days as of a mid-day bar —
    the current day's resampled row holds the EOD close (future data)."""
    from tools.backtest_replay import _daily_closes_asof
    idx = pd.date_range("2026-06-01", periods=96, freq="1h", tz="UTC")
    closes = pd.Series(range(96), index=idx, dtype=float)
    daily = closes.resample("1D").last()
    # cutoff mid-day on Jun 3 → only Jun 1 and Jun 2 rows are complete
    cutoff = pd.Timestamp("2026-06-03 10:00", tz="UTC")
    sliced = _daily_closes_asof(daily, cutoff)
    assert len(sliced) == 2
    assert sliced.index[-1] == pd.Timestamp("2026-06-02", tz="UTC")


# ─── Finding 6 — water mark must not ratchet off pre-entry bar extremes ────

def test_water_mark_uses_close_not_bar_extreme():
    """Ratchet from close only: the forming bar's high/low includes
    pre-entry price action that must not arm the trail."""
    from breakout_main import _update_water_mark
    pos = {"entry_price": 100.0}
    # bar spiked to 106 BEFORE entry; close is 100.5 — mark must not
    # jump to the pre-entry spike
    mark = _update_water_mark(pos, "LONG", current_close=100.5)
    assert mark == pytest.approx(100.5)
    mark = _update_water_mark(pos, "LONG", current_close=103.0)
    assert mark == pytest.approx(103.0)
    # retrace: mark must not fall
    mark = _update_water_mark(pos, "LONG", current_close=101.0)
    assert mark == pytest.approx(103.0)


def test_water_mark_short_uses_close():
    from breakout_main import _update_water_mark
    pos = {"entry_price": 100.0}
    assert _update_water_mark(pos, "SHORT", current_close=97.0) == 97.0
    assert _update_water_mark(pos, "SHORT", current_close=98.5) == 97.0


# ─── Finding 7 — SMA200 gate NaN edge + replay parity ──────────────────────

def test_sma200_gate_min_bars_covers_slope_lookback():
    """At the documented minimum window the slope read must be non-NaN
    (len 205 used to leave iloc[-7] inside the NaN warmup)."""
    from crossover_signals import SMA200_FILTER_MIN_BARS
    # need rolling(200) valid at iloc[-7]: len - 7 >= 199  →  len >= 206
    assert SMA200_FILTER_MIN_BARS >= 206


# ─── Finding 8 — P3.4 gates mirrored to the SHORT analyzer ─────────────────

def _mk_trend_df(n=260, up=True):
    base = 100.0
    closes = [base + (i * 0.3 if up else -i * 0.3) for i in range(n)]
    return pd.DataFrame({
        "close": closes,
        "high":  [c * 1.002 for c in closes],
        "low":   [c * 0.998 for c in closes],
        "open":  closes,
        "volume": [1000.0] * n,
    })


def test_short_analyzer_has_macd_zeroline_gate():
    """analyze_short_entry_signal must consult use_macd_zeroline_gate:
    SHORT wants the MACD line ABOVE zero (fading strength, not chasing
    an extended dump)."""
    import inspect
    from signals import analyze_short_entry_signal
    src = inspect.getsource(analyze_short_entry_signal)
    assert "use_macd_zeroline_gate" in src
    assert "use_ema200_alignment" in src


# ─── Finding 10 — whale week gate gets a config knob ───────────────────────

def test_whale_week_gate_config_constant_exists():
    from whale_config import WHALE_COHORT_REQUIRE_POSITIVE_WEEK
    assert isinstance(WHALE_COHORT_REQUIRE_POSITIVE_WEEK, bool)


def test_whale_week_gate_reads_config_default():
    import inspect
    from whale_signals import _qualifying_wallets
    sig = inspect.signature(_qualifying_wallets)
    default = sig.parameters["require_positive_week"].default
    from whale_config import WHALE_COHORT_REQUIRE_POSITIVE_WEEK
    assert default == WHALE_COHORT_REQUIRE_POSITIVE_WEEK
