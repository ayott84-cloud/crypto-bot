"""P3.2 — Scalp M.3 redesign.

Research basis: fixed 1.5% stops on 5m crypto sit inside wick noise
(11% live WR); the cost-floor literature says 5m tight-bracket OHLCV
strategies rarely clear fees. M.3 changes:
  - 15m timeframe (cost floor breathes; signals less noise-driven)
  - ATR-scaled bracket: SL = 2.5 x ATR(14), TP = 1.5R (research sweet
    spot; 2:1 needs 42%+ WR the entries can't sustain)
  - Fixed-dollar risk sizing: qty = risk_usd / stop_distance, capped
    by margin x leverage
  - Time-limit barrier (triple-barrier): close after N bars if neither
    bracket resolved — the entry edge has a decay horizon

Run: python -m pytest tests/test_scalp_m3.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest


def _cfg(**over):
    base = {
        "use_atr_bracket": True,
        "atr_sl_mult": 2.5,
        "tp_r_multiple": 1.5,
        "sl_pct": 1.5, "tp_pct": 3.0,   # legacy fallbacks
    }
    base.update(over)
    return base


# ─── atr_bracket_prices ────────────────────────────────────────────────────

def test_atr_bracket_long():
    from scalp_signals import atr_bracket_prices
    # entry 100, ATR 0.8 → SL = 100 - 2.5*0.8 = 98.0; risk 2.0; TP = 100+1.5*2 = 103
    sl, tp = atr_bracket_prices(100.0, "LONG", atr=0.8, cfg=_cfg())
    assert sl == pytest.approx(98.0)
    assert tp == pytest.approx(103.0)


def test_atr_bracket_short():
    from scalp_signals import atr_bracket_prices
    sl, tp = atr_bracket_prices(100.0, "SHORT", atr=0.8, cfg=_cfg())
    assert sl == pytest.approx(102.0)
    assert tp == pytest.approx(97.0)


def test_atr_bracket_falls_back_to_pct_when_atr_missing():
    from scalp_signals import atr_bracket_prices
    sl, tp = atr_bracket_prices(100.0, "LONG", atr=None, cfg=_cfg())
    assert sl == pytest.approx(98.5)   # legacy 1.5%
    assert tp == pytest.approx(103.0)  # legacy 3.0%


# ─── check_scalp_exit_atr ──────────────────────────────────────────────────

def test_exit_atr_long_sl_and_tp():
    from scalp_signals import check_scalp_exit_atr
    cfg = _cfg()
    # entry 100, ATR .8: SL 98, TP 103
    assert check_scalp_exit_atr(100.0, 97.9, "LONG", 0.8, cfg) == "SL Hit"
    assert check_scalp_exit_atr(100.0, 103.1, "LONG", 0.8, cfg) == "TP Hit"
    assert check_scalp_exit_atr(100.0, 100.5, "LONG", 0.8, cfg) is None


def test_exit_atr_short_mirrors():
    from scalp_signals import check_scalp_exit_atr
    cfg = _cfg()
    assert check_scalp_exit_atr(100.0, 102.1, "SHORT", 0.8, cfg) == "SL Hit"
    assert check_scalp_exit_atr(100.0, 96.9, "SHORT", 0.8, cfg) == "TP Hit"
    assert check_scalp_exit_atr(100.0, 99.5, "SHORT", 0.8, cfg) is None


# ─── risk-based sizing ─────────────────────────────────────────────────────

def test_risk_sized_qty_basic():
    from risk import risk_sized_qty
    # risk $2, stop distance $2/unit → 1 unit; notional 100 <= cap 100 OK
    qty = risk_sized_qty(risk_usd=2.0, entry_price=100.0, stop_price=98.0,
                           max_notional_usd=100.0)
    assert qty == pytest.approx(1.0)


def test_risk_sized_qty_caps_at_max_notional():
    from risk import risk_sized_qty
    # tiny stop distance would give huge qty → capped by notional
    qty = risk_sized_qty(risk_usd=2.0, entry_price=100.0, stop_price=99.9,
                           max_notional_usd=100.0)
    assert qty == pytest.approx(1.0)   # 100 notional / 100 price


def test_risk_sized_qty_zero_on_bad_inputs():
    from risk import risk_sized_qty
    assert risk_sized_qty(2.0, 100.0, 100.0, 100.0) == 0.0   # zero distance
    assert risk_sized_qty(0.0, 100.0, 98.0, 100.0) == 0.0    # zero risk
    assert risk_sized_qty(2.0, 0.0, -2.0, 100.0) == 0.0      # nonsense prices


# ─── time-limit barrier ────────────────────────────────────────────────────

def test_time_limit_exceeded():
    from scalp_signals import time_limit_exceeded
    from datetime import datetime, timedelta, timezone
    opened = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    # 16 bars x 15m = 4h — 5h elapsed → exceeded
    assert time_limit_exceeded(opened, interval="15m", limit_bars=16) is True


def test_time_limit_not_exceeded():
    from scalp_signals import time_limit_exceeded
    from datetime import datetime, timedelta, timezone
    opened = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    assert time_limit_exceeded(opened, interval="15m", limit_bars=16) is False


def test_time_limit_graceful_on_bad_timestamp():
    from scalp_signals import time_limit_exceeded
    assert time_limit_exceeded("not-a-date", "15m", 16) is False
    assert time_limit_exceeded(None, "15m", 16) is False


# ─── M.3 config defaults ───────────────────────────────────────────────────

def test_m3_config_defaults():
    import scalp_config as sc
    sample = next(iter(sc.SCALP_ASSETS.values()))
    assert sample.get("interval") == "15m"
    assert sample.get("use_atr_bracket") is True
    assert float(sample.get("atr_sl_mult", 0)) == pytest.approx(2.5)
    assert float(sample.get("tp_r_multiple", 0)) == pytest.approx(1.5)
    assert int(sample.get("time_limit_bars", 0)) > 0
    assert sample.get("use_daily_regime") is True
