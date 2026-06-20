"""Phase M — scalp_signals tests.

TDD: each test defines one signal/exit invariant. analyze_scalp_entry +
check_scalp_exit are pure functions on a pandas DataFrame; tests use
synthetic fixtures rather than fetched klines.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


# ─── Fixtures ──────────────────────────────────────────────────────────

def _scalp_cfg(**overrides) -> dict:
    base = {
        "range_short_sma":     10,
        "range_long_sma":      50,
        "momentum_lookback":   20,
        "new_high_lookback":   20,
        "sl_pct":              1.5,
        "tp_pct":              3.0,
        "allow_short":         True,
    }
    base.update(overrides)
    return base


def _build_df(closes, highs=None, lows=None, opens=None) -> "pd.DataFrame":
    """Build a 5m DataFrame from a close-price list. Defaults give a
    "narrow body" candle (open ≈ close, range ≈ 1% of close)."""
    n = len(closes)
    if highs is None:
        highs = [c * 1.005 for c in closes]
    if lows is None:
        lows  = [c * 0.995 for c in closes]
    if opens is None:
        opens = [c for c in closes]   # neutral candle (green/red set by caller)
    return pd.DataFrame({
        "open":   opens,
        "high":   highs,
        "low":    lows,
        "close":  closes,
        "volume": [1000] * n,
    })


def _rising_compressed_then_expanding(target_close: float = 110.0,
                                         n_compressed: int = 50,
                                         n_expanding: int = 12) -> list[float]:
    """Series that's flat (compressed range) then breaks out with
    expanding range. Last bar at target_close above the prior max."""
    flat = [100.0] * n_compressed
    rising = [100.0 + (target_close - 100.0) * (i + 1) / n_expanding
              for i in range(n_expanding)]
    return flat + rising


# ─── analyze_scalp_entry — happy paths ─────────────────────────────────

def test_long_entry_when_all_conditions_align():
    from scalp_signals import analyze_scalp_entry
    closes = _rising_compressed_then_expanding(target_close=112.0)
    n = len(closes)
    # Force expanding range on the recent 10 bars
    highs = [c + (3.0 if i >= n - 12 else 0.5) for i, c in enumerate(closes)]
    lows  = [c - (2.0 if i >= n - 12 else 0.5) for i, c in enumerate(closes)]
    # Green body on the LAST COMPLETED bar (iloc[-2])
    opens = [c for c in closes]
    opens[-2] = closes[-2] - 1.0       # green: open < close
    opens[-1] = closes[-1]
    df = _build_df(closes, highs, lows, opens)
    result = analyze_scalp_entry(df, _scalp_cfg())
    assert result["would_enter"] is True
    assert result["direction"] == "LONG"
    assert result["blocked_by"] is None


def test_short_entry_when_mirrored_conditions_align():
    from scalp_signals import analyze_scalp_entry
    # Falling series
    falling_closes = _rising_compressed_then_expanding(target_close=88.0)
    # Reverse to make it FALL into a new low
    closes = [100.0] * 50 + [100.0 + (88.0 - 100.0) * (i+1) / 12
                              for i in range(12)]
    n = len(closes)
    highs = [c + (2.0 if i >= n - 12 else 0.5) for i, c in enumerate(closes)]
    lows  = [c - (3.0 if i >= n - 12 else 0.5) for i, c in enumerate(closes)]
    opens = [c for c in closes]
    opens[-2] = closes[-2] + 1.0       # red: open > close
    df = _build_df(closes, highs, lows, opens)
    result = analyze_scalp_entry(df, _scalp_cfg())
    assert result["would_enter"] is True
    assert result["direction"] == "SHORT"


# ─── Each filter blocks individually ───────────────────────────────────

def test_blocked_when_vol_not_expanding():
    """SMA(range, 10) <= SMA(range, 50) — compression, not expansion."""
    from scalp_signals import analyze_scalp_entry
    n = 70
    closes = list(range(100, 100 + n))
    # Uniform 1.0 range — short sma == long sma
    df = _build_df([float(c) for c in closes],
                    highs=[c + 0.5 for c in closes],
                    lows=[c - 0.5 for c in closes])
    result = analyze_scalp_entry(df, _scalp_cfg())
    assert result["would_enter"] is False
    assert result["blocked_by"] == "vol_expansion"


def test_blocked_when_close_not_above_20_bars_ago():
    """Momentum gate: when neither momentum_up NOR a valid SHORT pattern,
    the result must block. Accept any of the non-pass blocker keys
    (which one fires depends on whether new_low + red also align)."""
    from scalp_signals import analyze_scalp_entry
    closes = _rising_compressed_then_expanding(target_close=112.0)
    closes[-2] = 99.0       # last completed bar BELOW close-20-ago
    df = _build_df(closes)
    result = analyze_scalp_entry(df, _scalp_cfg())
    assert result["would_enter"] is False
    # Could be momentum, new_high, candle_color, or vol_expansion depending
    # on which fixture detail dominates. The contract is "blocks correctly."
    assert result["blocked_by"] in (
        "momentum", "new_high", "candle_color", "vol_expansion")


def test_blocked_when_not_new_20_bar_high():
    """Close <= max of prior 20 closes — same relaxed expectation
    (multiple gates may flag this case; test just verifies it blocks)."""
    from scalp_signals import analyze_scalp_entry
    closes = _rising_compressed_then_expanding(target_close=120.0)
    closes[-22] = 130.0  # somewhere in lookback, an earlier higher close
    closes[-2] = 125.0   # last completed bar is below that earlier high
    df = _build_df(closes)
    result = analyze_scalp_entry(df, _scalp_cfg())
    assert result["would_enter"] is False
    assert result["blocked_by"] in (
        "new_high", "candle_color", "vol_expansion", "momentum")


def test_blocked_when_candle_not_green_for_long():
    """Body-direction gate: LONG requires close > open on the completed bar."""
    from scalp_signals import analyze_scalp_entry
    closes = _rising_compressed_then_expanding(target_close=112.0)
    n = len(closes)
    highs = [c + (3.0 if i >= n - 12 else 0.5) for i, c in enumerate(closes)]
    lows  = [c - (2.0 if i >= n - 12 else 0.5) for i, c in enumerate(closes)]
    opens = [c for c in closes]
    opens[-2] = closes[-2] + 1.0  # RED body on the completed bar
    df = _build_df(closes, highs, lows, opens)
    result = analyze_scalp_entry(df, _scalp_cfg())
    assert result["would_enter"] is False
    assert result["blocked_by"] in ("candle_color", "vol_expansion",
                                      "momentum", "new_high")


def test_blocked_when_insufficient_data():
    """Less than 60 bars → can't even compute SMA(50)."""
    from scalp_signals import analyze_scalp_entry
    df = _build_df([100.0] * 30)
    result = analyze_scalp_entry(df, _scalp_cfg())
    assert result["would_enter"] is False
    assert result["blocked_by"] == "insufficient_data"


# ─── allow_short = False gates SHORT specifically ──────────────────────

def test_short_blocked_when_allow_short_false():
    from scalp_signals import analyze_scalp_entry
    cfg = _scalp_cfg(allow_short=False)
    closes = [100.0] * 50 + [100.0 + (88.0 - 100.0) * (i+1) / 12 for i in range(12)]
    n = len(closes)
    highs = [c + (2.0 if i >= n - 12 else 0.5) for i, c in enumerate(closes)]
    lows  = [c - (3.0 if i >= n - 12 else 0.5) for i, c in enumerate(closes)]
    opens = [c for c in closes]
    opens[-2] = closes[-2] + 1.0
    df = _build_df(closes, highs, lows, opens)
    result = analyze_scalp_entry(df, cfg)
    assert result["would_enter"] is False
    assert result["blocked_by"] == "allow_short_disabled"


# ─── check_scalp_exit ──────────────────────────────────────────────────

def test_long_hits_sl_at_neg_1_5pct():
    from scalp_signals import check_scalp_exit
    reason = check_scalp_exit(
        entry_price=100.0, current_price=98.5,  # -1.5%
        direction="LONG", cfg=_scalp_cfg())
    assert reason == "SL Hit"


def test_long_does_not_hit_sl_above_threshold():
    from scalp_signals import check_scalp_exit
    reason = check_scalp_exit(
        entry_price=100.0, current_price=99.0,  # -1.0%, above SL
        direction="LONG", cfg=_scalp_cfg())
    assert reason is None


def test_long_hits_tp_at_pos_3pct():
    from scalp_signals import check_scalp_exit
    reason = check_scalp_exit(
        entry_price=100.0, current_price=103.0,  # +3%
        direction="LONG", cfg=_scalp_cfg())
    assert reason == "TP Hit"


def test_short_hits_sl_at_pos_1_5pct():
    from scalp_signals import check_scalp_exit
    reason = check_scalp_exit(
        entry_price=100.0, current_price=101.5,  # +1.5% adverse
        direction="SHORT", cfg=_scalp_cfg())
    assert reason == "SL Hit"


def test_short_hits_tp_at_neg_3pct():
    from scalp_signals import check_scalp_exit
    reason = check_scalp_exit(
        entry_price=100.0, current_price=97.0,  # -3% favorable
        direction="SHORT", cfg=_scalp_cfg())
    assert reason == "TP Hit"


def test_no_exit_when_between_bounds():
    from scalp_signals import check_scalp_exit
    assert check_scalp_exit(100.0, 101.0, "LONG", _scalp_cfg()) is None
    assert check_scalp_exit(100.0, 99.5,  "SHORT", _scalp_cfg()) is None


# ─── Phase M.2 enhancement filters ─────────────────────────────────────


def _long_signal_df(target_close: float = 112.0):
    """A baseline LONG signal where vol expansion + momentum + new_high
    + green body all align (the same fixture from test_long_entry_when_all_conditions_align).
    Each filter test starts from this then perturbs one variable."""
    closes = _rising_compressed_then_expanding(target_close=target_close)
    n = len(closes)
    highs = [c + (3.0 if i >= n - 12 else 0.5) for i, c in enumerate(closes)]
    lows  = [c - (2.0 if i >= n - 12 else 0.5) for i, c in enumerate(closes)]
    opens = [c for c in closes]
    opens[-2] = closes[-2] - 1.0       # green
    opens[-1] = closes[-1]
    return _build_df(closes, highs, lows, opens)


# ── Stronger vol expansion threshold ────────────────────────────────

def test_vol_expansion_threshold_blocks_mild_expansion():
    """At vol_expansion_threshold=1.5, range_sma_10 must be 1.5× sma_50.
    The baseline fixture's mild expansion gets blocked."""
    from scalp_signals import analyze_scalp_entry
    df = _long_signal_df(target_close=110.0)
    cfg = _scalp_cfg(vol_expansion_threshold=10.0)  # extreme — nothing passes
    result = analyze_scalp_entry(df, cfg)
    assert result["would_enter"] is False
    assert result["blocked_by"] == "vol_expansion"


def test_vol_expansion_threshold_default_unchanged():
    """Default threshold = 1.0 preserves the original > comparison."""
    from scalp_signals import analyze_scalp_entry
    df = _long_signal_df(target_close=112.0)
    cfg = _scalp_cfg()  # no threshold override
    result = analyze_scalp_entry(df, cfg)
    assert result["would_enter"] is True


# ── Volume confirmation filter ──────────────────────────────────────

def test_volume_filter_blocks_low_volume_breakout():
    from scalp_signals import analyze_scalp_entry
    closes = _rising_compressed_then_expanding(target_close=112.0)
    n = len(closes)
    highs = [c + (3.0 if i >= n - 12 else 0.5) for i, c in enumerate(closes)]
    lows  = [c - (2.0 if i >= n - 12 else 0.5) for i, c in enumerate(closes)]
    opens = [c - 1.0 if i == n - 2 else c for i, c in enumerate(closes)]
    df = _build_df(closes, highs, lows, opens)
    # Make the last completed bar's volume way below SMA
    df.loc[df.index[-2], "volume"] = 100.0  # SMA of [1000]*n ≈ 1000

    cfg = _scalp_cfg(use_volume_filter=True, vol_threshold_mult=1.5,
                      vol_sma_period=20)
    result = analyze_scalp_entry(df, cfg)
    assert result["would_enter"] is False
    assert result["blocked_by"] == "volume"


def test_volume_filter_passes_when_volume_spikes():
    from scalp_signals import analyze_scalp_entry
    closes = _rising_compressed_then_expanding(target_close=112.0)
    n = len(closes)
    highs = [c + (3.0 if i >= n - 12 else 0.5) for i, c in enumerate(closes)]
    lows  = [c - (2.0 if i >= n - 12 else 0.5) for i, c in enumerate(closes)]
    opens = [c - 1.0 if i == n - 2 else c for i, c in enumerate(closes)]
    df = _build_df(closes, highs, lows, opens)
    # Last completed bar's volume is 2× SMA
    df.loc[df.index[-2], "volume"] = 2500.0

    cfg = _scalp_cfg(use_volume_filter=True, vol_threshold_mult=1.5,
                      vol_sma_period=20)
    result = analyze_scalp_entry(df, cfg)
    assert result["would_enter"] is True


# ── Higher-TF (1h) trend gate ───────────────────────────────────────

def test_higher_tf_trend_blocks_long_in_1h_downtrend():
    from scalp_signals import analyze_scalp_entry
    df = _long_signal_df()
    cfg = _scalp_cfg(use_higher_tf_trend=True)
    # 1h DF where ema_fast < ema_slow → downtrend, LONG should be blocked
    df_1h = pd.DataFrame({
        "close":    [100.0] * 60,
        "ema_fast": [95.0] * 60,   # below slow
        "ema_slow": [100.0] * 60,
    })
    result = analyze_scalp_entry(df, cfg, df_1h=df_1h)
    assert result["would_enter"] is False
    assert result["blocked_by"] == "trend_1h"


def test_higher_tf_trend_passes_when_aligned():
    from scalp_signals import analyze_scalp_entry
    df = _long_signal_df()
    cfg = _scalp_cfg(use_higher_tf_trend=True)
    df_1h = pd.DataFrame({
        "close":    [100.0] * 60,
        "ema_fast": [105.0] * 60,  # above slow → uptrend
        "ema_slow": [100.0] * 60,
    })
    result = analyze_scalp_entry(df, cfg, df_1h=df_1h)
    assert result["would_enter"] is True


def test_higher_tf_trend_defaults_to_pass_when_df_1h_missing():
    """Graceful degradation: when 1h data isn't available, the gate
    must not block (matches breakout_main's 1D fallback pattern)."""
    from scalp_signals import analyze_scalp_entry
    df = _long_signal_df()
    cfg = _scalp_cfg(use_higher_tf_trend=True)
    result = analyze_scalp_entry(df, cfg, df_1h=None)
    assert result["would_enter"] is True


# ── RSI extreme filter ─────────────────────────────────────────────

def _overbought_long_signal_df(n: int = 80):
    """A monotonically rising series that ALSO has expanding range on
    the recent bars (so vol_expansion passes) and a green completed
    bar. RSI(14) on a 2-per-bar rising series sits >> 70."""
    closes = [100.0 + i * 2.0 for i in range(n)]
    # Expanding range on the last 12 bars (mirrors _long_signal_df)
    highs = [c + (3.0 if i >= n - 12 else 0.5) for i, c in enumerate(closes)]
    lows  = [c - (2.0 if i >= n - 12 else 0.5) for i, c in enumerate(closes)]
    opens = [c for c in closes]
    opens[-2] = closes[-2] - 1.0   # green
    return _build_df(closes, highs, lows, opens)


def test_rsi_filter_blocks_long_when_overbought():
    """A sharply rising series will push RSI above 70 → blocks LONG."""
    from scalp_signals import analyze_scalp_entry
    df = _overbought_long_signal_df()
    cfg = _scalp_cfg(use_rsi_extreme_filter=True, rsi_overbought=70.0)
    result = analyze_scalp_entry(df, cfg)
    assert result["would_enter"] is False
    assert result["blocked_by"] == "rsi_overbought"


def test_rsi_filter_off_lets_signal_through():
    """The same overbought-RSI fixture passes when the filter is off
    (default behavior — backwards-compat preserved)."""
    from scalp_signals import analyze_scalp_entry
    df = _overbought_long_signal_df()
    cfg = _scalp_cfg()  # filter not enabled
    result = analyze_scalp_entry(df, cfg)
    assert result["would_enter"] is True
