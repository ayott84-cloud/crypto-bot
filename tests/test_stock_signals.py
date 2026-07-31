"""Module 2 Phase S2 — the three daily-bar equity sleeves.

Pure functions only. Same diagnostic-dict shape as
signals.analyze_entry_signal / breakout_signals.analyze_breakout_entry
so the replay can import LIVE code (the invariant that makes
backtest/live drift impossible) and so signal_status feeds the
dashboard's why-silent panel unchanged.

  StockTrend  Faber time-series momentum: hold while close > 200d SMA,
              else cash. Long/flat. Tier-A evidence, ~2-6 trades/yr.
  StockDual   Antonacci dual momentum, run as an ENSEMBLE over several
              lookbacks — ReSolve tested 1,226 specs and found the
              canonical 12/1 beat only 61% of its own siblings, i.e.
              single-spec choice is luck. A cash leg is mandatory: in
              2022 stocks AND bonds fell together and the classic
              "safe harbour" was itself a loser.
  StockRev    IBS / RSI(2) short-term reversion, index ETFs only,
              long/flat. Tier-B and decaying since ~2013, kept for its
              negative correlation to the trend sleeve, not its return.

All three are LONG/FLAT BY DESIGN: no shorting means no Reg SHO
short-sale restriction, no locates, no borrow fees. That is a scope
decision, not an oversight.

Run: python -m pytest tests/test_stock_signals.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

import stock_signals as ss


def _frame(closes, highs=None, lows=None, opens=None):
    n = len(closes)
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame({
        "open":  opens if opens is not None else closes,
        "high":  highs if highs is not None else [c * 1.01 for c in closes],
        "low":   lows if lows is not None else [c * 0.99 for c in closes],
        "close": closes,
        "volume": [1e6] * n,
    }, index=idx)


# ─── StockTrend ───────────────────────────────────────────────────────────

_TREND_CFG = {"sma_period": 200, "strategy_name": "SPY 1M StockTrend"}


def test_trend_holds_when_above_sma():
    df = _frame(list(np.linspace(100, 200, 260)))       # steady uptrend
    sig = ss.analyze_trend_entry(df, _TREND_CFG)
    assert sig["would_enter"] is True
    assert sig["direction"] == "LONG"
    assert sig["blocked_by"] is None
    assert sig["values"]["close"] > sig["values"]["sma"]


def test_trend_stands_aside_when_below_sma():
    df = _frame(list(np.linspace(200, 100, 260)))       # steady downtrend
    sig = ss.analyze_trend_entry(df, _TREND_CFG)
    assert sig["would_enter"] is False
    assert sig["blocked_by"] == "below_sma"
    assert sig["direction"] is None


def test_trend_never_signals_short():
    """Long/flat by design — a SHORT would drag in Reg SHO, locates and
    borrow fees that Module 2 deliberately does not model."""
    df = _frame(list(np.linspace(200, 100, 260)))
    assert ss.analyze_trend_entry(df, _TREND_CFG)["direction"] != "SHORT"


def test_trend_blocks_on_insufficient_history():
    """199 bars cannot produce a 200-day SMA. Silently using a shorter
    window would make the first months of every backtest a different
    strategy."""
    df = _frame(list(np.linspace(100, 120, 150)))
    sig = ss.analyze_trend_entry(df, _TREND_CFG)
    assert sig["would_enter"] is False
    assert sig["blocked_by"] == "insufficient_history"


def test_trend_exit_when_close_breaks_below_sma():
    df = _frame(list(np.linspace(200, 100, 260)))
    reason = ss.check_trend_exit(df, _TREND_CFG)
    assert reason == "Below SMA"


def test_trend_holds_position_while_above_sma():
    df = _frame(list(np.linspace(100, 200, 260)))
    assert ss.check_trend_exit(df, _TREND_CFG) is None


def test_trend_uses_completed_bars_only():
    """The last row of a live frame is a FORMING bar. Acting on it is
    repainting: the signal changes intraday and the backtest cannot be
    reproduced. Both entry and exit must read the last COMPLETED bar."""
    closes = list(np.linspace(100, 200, 260))
    df = _frame(closes)
    spike = df.copy()
    spike.iloc[-1, spike.columns.get_loc("close")] = 1.0    # absurd forming bar
    assert (ss.analyze_trend_entry(df, _TREND_CFG)["values"]["close"]
            == ss.analyze_trend_entry(spike, _TREND_CFG)["values"]["close"])


# ─── StockDual (ensemble) ─────────────────────────────────────────────────

def _ramp(start, end, n=300):
    return _frame(list(np.linspace(start, end, n)))


_DUAL_CFG = {
    "risk_assets": ["SPY", "VEU"],
    "safe_asset": "AGG",
    "cash_asset": "BIL",
    "lookbacks_months": [3, 6, 9, 12],
    "strategy_name": "GEM 1M StockDual",
}


def test_dual_picks_the_strongest_risk_asset():
    frames = {
        "SPY": _ramp(100, 200),      # +100%
        "VEU": _ramp(100, 120),      # +20%
        "AGG": _ramp(100, 101),
        "BIL": _ramp(100, 100.5),
    }
    out = ss.dual_momentum_vote(frames, _DUAL_CFG)
    assert out["winner"] == "SPY"
    assert out["ensemble_agreement"] == pytest.approx(1.0)


def test_dual_goes_defensive_when_risk_assets_lag_cash():
    frames = {
        "SPY": _ramp(200, 150),      # falling
        "VEU": _ramp(200, 160),      # falling
        "AGG": _ramp(100, 108),
        "BIL": _ramp(100, 105),      # cash beats both risk assets
    }
    out = ss.dual_momentum_vote(frames, _DUAL_CFG)
    assert out["winner"] in ("AGG", "BIL")
    assert out["absolute_momentum_ok"] is False


def test_dual_2022_regime_falls_through_to_cash_not_bonds():
    """2022 broke classic GEM: stocks AND bonds fell together, so the
    'safe' leg was also a loser. The cash leg exists for exactly this."""
    frames = {
        "SPY": _ramp(200, 150),
        "VEU": _ramp(200, 155),
        "AGG": _ramp(100, 85),       # bonds down too
        "BIL": _ramp(100, 102),      # only cash is up
    }
    out = ss.dual_momentum_vote(frames, _DUAL_CFG)
    assert out["winner"] == "BIL", "fell into losing bonds instead of cash"


def test_dual_reports_ensemble_dispersion_not_just_the_winner():
    """ReSolve: the 5-year return spread between a lucky and unlucky
    spec averaged 64 points. A vote we cannot see the dispersion of is
    a single spec wearing a disguise."""
    # SPY: deep decline then a sharp recovery -> strong on SHORT
    # lookbacks, still weak over 12 months. VEU: steady grind -> wins the
    # long lookback only. This is the shape where spec choice decides the
    # answer, which is the whole reason for an ensemble.
    frames = {
        "SPY": _frame(list(np.concatenate([np.linspace(100, 70, 200),
                                             np.linspace(70, 100, 100)]))),
        "VEU": _ramp(100, 115),
        "AGG": _ramp(100, 101),
        "BIL": _ramp(100, 100.5),
    }
    out = ss.dual_momentum_vote(frames, _DUAL_CFG)
    assert set(out["per_lookback"]) == {3, 6, 9, 12}
    assert 0.0 <= out["ensemble_agreement"] < 1.0
    assert len(set(out["per_lookback"].values())) > 1, (
        f"lookbacks all agreed ({out['per_lookback']}) — fixture does not "
        "exercise dispersion")


def test_dual_requires_history_for_the_longest_lookback():
    frames = {k: _ramp(100, 110, n=60) for k in ("SPY", "VEU", "AGG", "BIL")}
    out = ss.dual_momentum_vote(frames, _DUAL_CFG)
    assert out["winner"] is None
    assert out["blocked_by"] == "insufficient_history"


def test_dual_missing_symbol_is_blocked_not_guessed():
    frames = {"SPY": _ramp(100, 200), "AGG": _ramp(100, 101),
               "BIL": _ramp(100, 100.5)}          # VEU absent
    out = ss.dual_momentum_vote(frames, _DUAL_CFG)
    assert out["blocked_by"] == "missing_data"
    assert out["winner"] is None


# ─── StockRev ─────────────────────────────────────────────────────────────

_REV_CFG = {
    "sma_period": 200, "ibs_threshold": 0.2, "rsi_period": 2,
    "rsi_threshold": 10.0, "exit_sma_period": 5, "rsi_exit": 70.0,
    "max_hold_bars": 10, "strategy_name": "QQQ 1D StockRev",
}


def test_ibs_is_position_within_the_bar_range():
    df = pd.DataFrame({"high": [10.0], "low": [8.0], "close": [8.4]})
    assert ss.ibs(df).iloc[-1] == pytest.approx(0.2)


def test_ibs_handles_zero_range_bar():
    """A limit-locked bar has high == low; naive division is a ZeroDivision
    or a silent NaN that propagates into the signal."""
    df = pd.DataFrame({"high": [10.0], "low": [10.0], "close": [10.0]})
    v = ss.ibs(df).iloc[-1]
    assert 0.0 <= v <= 1.0


def test_reversion_enters_on_low_ibs_in_an_uptrend():
    closes = list(np.linspace(100, 200, 250))
    df = _frame(closes)
    # make the last COMPLETED bar close at its low (IBS ~ 0)
    i = len(df) - 2
    df.iloc[i, df.columns.get_loc("low")] = closes[i] - 5
    df.iloc[i, df.columns.get_loc("high")] = closes[i] + 5
    df.iloc[i, df.columns.get_loc("close")] = closes[i] - 4.9
    sig = ss.analyze_reversion_entry(df, _REV_CFG)
    assert sig["would_enter"] is True
    assert sig["direction"] == "LONG"


def test_reversion_blocked_below_the_200d_sma():
    """The regime filter is what keeps 'buy the dip' from becoming
    'catch the knife' — dip-buying below the 200d is where the
    published decay concentrates."""
    closes = list(np.linspace(200, 100, 250))
    df = _frame(closes)
    i = len(df) - 2
    df.iloc[i, df.columns.get_loc("low")] = closes[i] - 5
    df.iloc[i, df.columns.get_loc("close")] = closes[i] - 4.9
    sig = ss.analyze_reversion_entry(df, _REV_CFG)
    assert sig["would_enter"] is False
    assert sig["blocked_by"] == "below_sma"


def test_reversion_blocked_when_not_oversold():
    df = _frame(list(np.linspace(100, 200, 250)))
    sig = ss.analyze_reversion_entry(df, _REV_CFG)
    assert sig["would_enter"] is False
    assert sig["blocked_by"] == "not_oversold"


def test_reversion_exits_on_time_stop():
    """Isolate the time stop: a DECLINING series keeps the close below
    its 5-day SMA and RSI(2) near zero, so neither higher-precedence
    exit can fire. (A rising series would exit on 'Above SMA' first —
    correctly — and prove nothing about the time stop.)"""
    df = _frame(list(np.linspace(120, 100, 250)))
    assert ss.check_reversion_exit(df, _REV_CFG, bars_held=9,
                                     entry_price=110.0) is None
    reason = ss.check_reversion_exit(df, _REV_CFG, bars_held=10,
                                       entry_price=110.0)
    assert reason == "Time Stop"


def test_reversion_exit_precedence_is_deterministic():
    """Two exits can be true on the same bar; the order must be fixed or
    the journal's exit_reason distribution is noise."""
    closes = list(np.linspace(100, 140, 250))
    df = _frame(closes)
    reason = ss.check_reversion_exit(df, _REV_CFG, bars_held=99,
                                       entry_price=100.0)
    assert reason in ("Above SMA", "RSI Exit", "Time Stop")
    # whatever fires, it must be stable across identical calls
    assert reason == ss.check_reversion_exit(df, _REV_CFG, bars_held=99,
                                               entry_price=100.0)


def test_rsi_bounds():
    up = ss.rsi(_frame(list(np.linspace(100, 200, 60)))["close"], 2)
    down = ss.rsi(_frame(list(np.linspace(200, 100, 60)))["close"], 2)
    assert up.iloc[-1] > 90
    assert down.iloc[-1] < 10


# ─── Shape contract shared with the crypto analyzers ─────────────────────

@pytest.mark.parametrize("fn,cfg", [
    ("analyze_trend_entry", _TREND_CFG),
    ("analyze_reversion_entry", _REV_CFG),
])
def test_analyzers_return_the_house_diagnostic_shape(fn, cfg):
    df = _frame(list(np.linspace(100, 200, 260)))
    sig = getattr(ss, fn)(df, cfg)
    for key in ("would_enter", "direction", "blocked_by", "filters", "values"):
        assert key in sig, f"{fn} missing {key}"
    assert isinstance(sig["filters"], dict)
    assert isinstance(sig["values"], dict)
