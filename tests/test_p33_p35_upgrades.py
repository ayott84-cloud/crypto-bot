"""P3.3 + P3.4 + P3.5 — breakout/momentum/whale research upgrades.

P3.3 Breakout: funding veto (don't buy crowded longs) + offset-armed
     trailing exit (protect the runners that pay for low WR).
P3.4 Momentum: MACD zero-line-side gate + EMA200 alignment (TradingRush
     62% WR template — the two details most MACD bots omit).
P3.5 Whale: multi-window cohort persistence (positive week AND month —
     <15% of monthly top-10 repeat; single-window winners mean-revert).

Run: python -m pytest tests/test_p33_p35_upgrades.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


# ─── P3.3a — funding veto ──────────────────────────────────────────────────

def test_funding_veto_blocks_crowded_long():
    from breakout_signals import check_funding_veto
    ok, reason = check_funding_veto("LONG", funding_rate_8h=0.0006)
    assert ok is False
    assert "crowded" in reason.lower()


def test_funding_veto_blocks_crowded_short():
    from breakout_signals import check_funding_veto
    ok, _ = check_funding_veto("SHORT", funding_rate_8h=-0.0006)
    assert ok is False


def test_funding_veto_passes_normal_funding():
    from breakout_signals import check_funding_veto
    assert check_funding_veto("LONG", 0.0001)[0] is True
    assert check_funding_veto("SHORT", -0.0001)[0] is True
    assert check_funding_veto("LONG", -0.0008)[0] is True   # negative funding fine for LONG


def test_funding_veto_passes_on_missing_data():
    from breakout_signals import check_funding_veto
    assert check_funding_veto("LONG", None)[0] is True


# ─── P3.3b — offset-armed trailing exit ────────────────────────────────────

def test_trailing_not_armed_before_activation():
    from breakout_signals import check_trailing_exit
    cfg = {"trail_arm_atr_mult": 1.5, "trail_atr_mult": 1.0}
    # hwm only 1x ATR above entry — not armed; deep pullback ignored
    assert check_trailing_exit("LONG", entry_price=100.0, high_water_mark=102.0,
                                  current_price=100.1, atr_at_entry=2.0,
                                  cfg=cfg) is None


def test_trailing_fires_after_armed_pullback():
    from breakout_signals import check_trailing_exit
    cfg = {"trail_arm_atr_mult": 1.5, "trail_atr_mult": 1.0}
    # hwm = 104 (2x ATR above entry, armed); trail = 104 - 1*2 = 102
    assert check_trailing_exit("LONG", entry_price=100.0, high_water_mark=104.0,
                                  current_price=101.9, atr_at_entry=2.0,
                                  cfg=cfg) == "Trailing Exit"


def test_trailing_holds_above_trail_line():
    from breakout_signals import check_trailing_exit
    cfg = {"trail_arm_atr_mult": 1.5, "trail_atr_mult": 1.0}
    assert check_trailing_exit("LONG", entry_price=100.0, high_water_mark=104.0,
                                  current_price=102.5, atr_at_entry=2.0,
                                  cfg=cfg) is None


def test_trailing_short_mirrors():
    from breakout_signals import check_trailing_exit
    cfg = {"trail_arm_atr_mult": 1.5, "trail_atr_mult": 1.0}
    # SHORT entry 100, low-water 96 (2x ATR below, armed); trail = 96 + 2 = 98
    assert check_trailing_exit("SHORT", entry_price=100.0, high_water_mark=96.0,
                                  current_price=98.1, atr_at_entry=2.0,
                                  cfg=cfg) == "Trailing Exit"
    assert check_trailing_exit("SHORT", entry_price=100.0, high_water_mark=96.0,
                                  current_price=97.0, atr_at_entry=2.0,
                                  cfg=cfg) is None


def test_trailing_graceful_on_missing_atr():
    from breakout_signals import check_trailing_exit
    assert check_trailing_exit("LONG", 100.0, 104.0, 101.0, None,
                                  {"trail_arm_atr_mult": 1.5}) is None


# ─── P3.4 — momentum MACD zero-line + EMA200 gates ─────────────────────────

def _mk_df(n=260, up=True):
    import numpy as np
    base = 100.0
    closes = [base + (i * 0.3 if up else -i * 0.3) for i in range(n)]
    df = pd.DataFrame({
        "close": closes,
        "high":  [c * 1.002 for c in closes],
        "low":   [c * 0.998 for c in closes],
        "open":  closes,
        "volume": [1000.0] * n,
    })
    return df


def test_ema200_alignment_gate_pure():
    from signals import ema200_alignment_ok
    df = _mk_df(up=True)
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    assert ema200_alignment_ok(df, "LONG") is True
    assert ema200_alignment_ok(df, "SHORT") is False


def test_ema200_alignment_gate_downtrend():
    from signals import ema200_alignment_ok
    df = _mk_df(up=False)
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    assert ema200_alignment_ok(df, "SHORT") is True
    assert ema200_alignment_ok(df, "LONG") is False


def test_ema200_alignment_pass_when_missing():
    from signals import ema200_alignment_ok
    df = _mk_df(n=50)   # too short for ema200 to be meaningful; no column
    assert ema200_alignment_ok(df, "LONG") is True


def test_macd_zeroline_side_gate():
    from signals import macd_zeroline_ok
    # LONG: MACD line should be BELOW zero (buying a pullback in an
    # uptrend, not chasing an extended move)
    assert macd_zeroline_ok("LONG", macd_line=-0.5) is True
    assert macd_zeroline_ok("LONG", macd_line=+0.5) is False
    assert macd_zeroline_ok("SHORT", macd_line=+0.5) is True
    assert macd_zeroline_ok("SHORT", macd_line=-0.5) is False
    assert macd_zeroline_ok("LONG", macd_line=None) is True   # degrade


# ─── P3.5 — whale multi-window persistence ─────────────────────────────────

def test_whale_qualifying_requires_positive_week_when_enabled():
    from whale_signals import _qualifying_wallets, MIN_ACCOUNT_VALUE_USD
    wallets = [
        {"address": "0x1", "account_value": MIN_ACCOUNT_VALUE_USD * 2,
         "pnl_alltime": 1e6, "pnl_month": 50_000, "pnl_week": -5_000},
        {"address": "0x2", "account_value": MIN_ACCOUNT_VALUE_USD * 2,
         "pnl_alltime": 1e6, "pnl_month": 50_000, "pnl_week": 10_000},
    ]
    out = _qualifying_wallets(wallets, require_positive_month=True,
                                 require_positive_week=True)
    assert {w["address"] for w in out} == {"0x2"}


def test_whale_qualifying_week_gate_passes_when_field_missing():
    """Wallets without pnl_week data (older parses) aren't excluded by
    the week gate — absence of data must not block."""
    from whale_signals import _qualifying_wallets, MIN_ACCOUNT_VALUE_USD
    wallets = [
        {"address": "0x1", "account_value": MIN_ACCOUNT_VALUE_USD * 2,
         "pnl_alltime": 1e6, "pnl_month": 50_000},
    ]
    out = _qualifying_wallets(wallets, require_positive_month=True,
                                 require_positive_week=True)
    assert len(out) == 1


def test_whale_parse_extracts_week_pnl():
    from whale_signals import _parse_leaderboard_entry
    entry = {
        "ethAddress": "0xabc", "displayName": "W",
        "accountValue": 500_000,
        "windowPerformances": [
            ["week",    {"pnl": 12_345}],
            ["month",   {"pnl": 60_000}],
            ["allTime", {"pnl": 900_000}],
        ],
    }
    parsed = _parse_leaderboard_entry(entry)
    assert parsed["pnl_week"] == 12_345.0
    assert parsed["pnl_month"] == 60_000.0


# ─── P3.3 wiring — breakout_main helpers ───────────────────────────────────

def test_parse_funding_rate_8h_from_weex_list():
    from breakout_main import _parse_funding_rate_8h
    raw = [{"symbol": "cmt_btcusdt", "fundingRate": "0.000125"}]
    assert _parse_funding_rate_8h(raw) == pytest.approx(0.000125)


def test_parse_funding_rate_8h_graceful_on_garbage():
    from breakout_main import _parse_funding_rate_8h
    assert _parse_funding_rate_8h([]) is None
    assert _parse_funding_rate_8h(None) is None
    assert _parse_funding_rate_8h([{"symbol": "x"}]) is None
    assert _parse_funding_rate_8h([{"fundingRate": "not-a-number"}]) is None


def test_update_water_mark_long_ratchets_up_only():
    from breakout_main import _update_water_mark
    pos = {"entry_price": 100.0}
    assert _update_water_mark(pos, "LONG", current_close=103.0) == 103.0
    assert pos["high_water_mark"] == 103.0
    # lower close must not lower the mark
    assert _update_water_mark(pos, "LONG", current_close=101.0) == 103.0


def test_update_water_mark_short_tracks_low():
    from breakout_main import _update_water_mark
    pos = {"entry_price": 100.0}
    assert _update_water_mark(pos, "SHORT", current_close=96.0) == 96.0
    # higher close must not raise the mark
    assert _update_water_mark(pos, "SHORT", current_close=97.5) == 96.0


def test_breakout_factory_has_p33_flags():
    from breakout_config import _breakout_default
    cfg = _breakout_default("BTCUSDT", "4h", "BTC 4H Breakout")
    assert cfg["use_funding_veto"] is True
    assert cfg["use_trailing_exit"] is True
    assert cfg["trail_arm_atr_mult"] == 1.5
    assert cfg["trail_atr_mult"] == 1.0
