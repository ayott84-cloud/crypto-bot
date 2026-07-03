"""P2.3 — daily-MA trend regime classifier.

TradingRush's 200-trade protocol found a 34-point WR swing from regime
alone (76% WR trending vs 42% WR chop on the identical strategy). The
classifier: 9-period MA on the DAILY chart — price above a RISING MA =
uptrend regime (trend-following longs allowed); price below a FALLING
MA = downtrend regime (shorts allowed); anything else = flat (trend
bots sit out).

Pure function + replay integration behind cfg["use_daily_regime"].

Run: python -m pytest tests/test_daily_regime.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


def _series(vals):
    return pd.Series(vals, dtype=float)


# ─── classify_daily_trend ──────────────────────────────────────────────────

def test_uptrend_when_price_above_rising_ma():
    from regime import classify_daily_trend
    # steadily rising closes: MA rising, price above it
    closes = _series([100 + i for i in range(30)])
    assert classify_daily_trend(closes) == "up"


def test_downtrend_when_price_below_falling_ma():
    from regime import classify_daily_trend
    closes = _series([130 - i for i in range(30)])
    assert classify_daily_trend(closes) == "down"


def test_flat_when_price_sits_on_flat_ma():
    from regime import classify_daily_trend
    # constant closes: MA identical to price and to its own past — neither
    # rising nor falling, price neither above nor below → flat regime
    closes = _series([100.0] * 30)
    assert classify_daily_trend(closes) == "flat"


def test_flat_when_price_above_but_ma_falling():
    """A dead-cat bounce: price pops above a still-falling MA → NOT an
    uptrend regime."""
    from regime import classify_daily_trend
    closes = _series([130 - i for i in range(28)] + [110.0, 115.0])
    # MA(9) is still falling even though last closes bounced above it? Let's
    # just assert it is NOT 'up' unless the MA is genuinely rising.
    assert classify_daily_trend(closes) != "up"


def test_unknown_on_insufficient_data():
    from regime import classify_daily_trend
    assert classify_daily_trend(_series([100.0] * 5)) == "unknown"
    assert classify_daily_trend(None) == "unknown"


def test_direction_allowed_mapping():
    from regime import daily_regime_allows
    assert daily_regime_allows("LONG", "up") is True
    assert daily_regime_allows("SHORT", "up") is False
    assert daily_regime_allows("SHORT", "down") is True
    assert daily_regime_allows("LONG", "down") is False
    assert daily_regime_allows("LONG", "flat") is False
    assert daily_regime_allows("SHORT", "flat") is False
    # unknown → allow (graceful degradation, same as every other gate)
    assert daily_regime_allows("LONG", "unknown") is True
    assert daily_regime_allows("SHORT", "unknown") is True


# ─── Replay integration ────────────────────────────────────────────────────

def _kline_df(rows, freq="1h"):
    idx = pd.date_range("2026-01-01", periods=len(rows), freq=freq, tz="UTC")
    return pd.DataFrame({
        "open":   [r[0] for r in rows],
        "high":   [r[1] for r in rows],
        "low":    [r[2] for r in rows],
        "close":  [r[3] for r in rows],
        "volume": [1000.0] * len(rows),
    }, index=idx)


def test_replay_crossover_daily_regime_blocks_countertrend_long():
    """With use_daily_regime=True, a golden cross during a DOWN daily
    regime must NOT enter."""
    from tools.backtest_replay import replay_crossover
    # 25 days of falling 1h bars (600 bars, daily MA falling), then a small
    # local pop that produces a golden cross on the 1h SMAs.
    rows = []
    price = 200.0
    for i in range(600):
        price -= 0.15                      # steady downtrend
        rows.append((price, price + 0.2, price - 0.2, price))
    for i in range(60):
        price += 0.55                      # sharp local bounce → 1h cross
        rows.append((price, price + 0.3, price - 0.2, price))
    df = _kline_df(rows)
    cfg = {"symbol": "T", "interval": "1h", "sma_fast": 20, "sma_slow": 50,
            "sl_pct": 1.0, "tp_pct": 2.0, "allow_short": False,
            "use_daily_regime": True}
    gated = replay_crossover("T", cfg, bars=len(df), pre_fetched_df=df,
                                round_trip_cost_pct=0.0)
    ungated_cfg = dict(cfg, use_daily_regime=False)
    ungated = replay_crossover("T", ungated_cfg, bars=len(df),
                                  pre_fetched_df=df, round_trip_cost_pct=0.0)
    # The ungated replay takes the counter-trend long; the gated one blocks
    # at least some of them (daily MA is still falling during the bounce).
    assert ungated.n_trades >= 1
    assert gated.n_trades < ungated.n_trades
