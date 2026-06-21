"""Whale E.1 — curated wallet whitelist tests.

Phase W enhancement beyond the U.1/U.2/U.3 unblocks: when
WHALE_WALLET_WHITELIST is non-empty, fetch_cohorts() scans those
addresses directly and ignores the leaderboard rank entirely for
the smart-money cohort. Eliminates survivorship bias by trusting
the operator's hand-curated cohort rather than HL's recency-biased
all-time-PnL sort.

Rekt cohort still comes from the leaderboard (rekt detection works
fine on raw sort).

Run: python -m pytest tests/test_whale_whitelist.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest


# ─── Shared fixtures ───────────────────────────────────────────────────────

def _make_lb_entry(addr, alltime_pnl=1_000_000, month_pnl=50_000,
                     account_val=200_000, name="Whale"):
    """Build the shape get_leaderboard() returns."""
    return {
        "ethAddress": addr, "displayName": name,
        "accountValue": account_val,
        "windowPerformances": [
            ["allTime", {"pnl": alltime_pnl}],
            ["month",   {"pnl": month_pnl}],
        ],
    }


def _make_positions(addr, account_val=200_000, has_positions=True,
                       positions=None):
    return {
        "address": addr,
        "has_positions": has_positions,
        "account_value": account_val,
        "positions": positions or [
            {"coin": "BTC", "direction": "LONG", "size": 0.5,
             "entry_price": 100_000, "unrealized_pnl": 5_000},
        ],
    }


# ─── Whitelist mode active ─────────────────────────────────────────────────

@patch("whale_signals.get_bulk_positions")
@patch("whale_signals.get_leaderboard")
def test_whitelist_active_uses_curated_addresses_only(mock_lb, mock_pos):
    """When WHALE_WALLET_WHITELIST is non-empty, smart_pool comes from it,
    NOT from leaderboard top-N."""
    from whale_signals import fetch_cohorts
    # 50 leaderboard wallets — none of which match our whitelist
    lb_rows = [
        _make_lb_entry(f"0x{i:040x}", alltime_pnl=10_000_000 - i, month_pnl=100)
        for i in range(50)
    ]
    mock_lb.return_value = lb_rows

    whitelisted = ["0xabc1", "0xabc2", "0xabc3"]
    mock_pos.side_effect = [
        # smart_positions — should be called with the whitelist
        [_make_positions(a) for a in whitelisted],
        # rekt_positions — leaderboard-based
        [_make_positions(f"0x{i:040x}", has_positions=False) for i in range(50)],
    ]

    with patch("whale_signals.WHALE_WALLET_WHITELIST", whitelisted):
        smart, rekt = fetch_cohorts(n=10)

    # Verify smart_pool actually passed to get_bulk_positions was the whitelist
    smart_call = mock_pos.call_args_list[0]
    smart_pool_arg = smart_call.args[0]
    assert smart_pool_arg == whitelisted
    # And the returned smart cohort matches
    assert {w["address"] for w in smart} == set(whitelisted)


@patch("whale_signals.get_bulk_positions")
@patch("whale_signals.get_leaderboard")
def test_whitelist_active_still_uses_leaderboard_for_rekt(mock_lb, mock_pos):
    """Rekt cohort detection remains leaderboard-driven (worst month PnL)."""
    from whale_signals import fetch_cohorts
    # 5 leaderboard rows; bottom 2 by month_pnl should be the rekt pool seed
    lb_rows = [
        _make_lb_entry("0xWin1", alltime_pnl=5_000_000, month_pnl=+200_000),
        _make_lb_entry("0xWin2", alltime_pnl=4_000_000, month_pnl=+100_000),
        _make_lb_entry("0xFlat", alltime_pnl=1_000_000, month_pnl=0),
        _make_lb_entry("0xRekt1", alltime_pnl=2_000_000, month_pnl=-150_000),
        _make_lb_entry("0xRekt2", alltime_pnl=2_500_000, month_pnl=-200_000),
    ]
    mock_lb.return_value = lb_rows
    whitelisted = ["0xCurated1", "0xCurated2"]

    # rekt_positions are returned in the order the leaderboard sorts them
    # (worst month_pnl first). Smart positions returned for curated wallets.
    mock_pos.side_effect = [
        [_make_positions(a) for a in whitelisted],
        [_make_positions("0xRekt2"), _make_positions("0xRekt1")],
    ]

    with patch("whale_signals.WHALE_WALLET_WHITELIST", whitelisted):
        smart, rekt = fetch_cohorts(n=2)

    rekt_addresses = {w["address"] for w in rekt}
    assert rekt_addresses == {"0xRekt1", "0xRekt2"}


@patch("whale_signals.get_bulk_positions")
@patch("whale_signals.get_leaderboard")
def test_whitelist_active_falls_back_meta_when_address_not_on_leaderboard(
        mock_lb, mock_pos):
    """A whitelisted wallet that ISN'T on the HL leaderboard gets default
    metadata ('Anonymous', 0, 0) — fetch shouldn't error."""
    from whale_signals import fetch_cohorts
    # Leaderboard has different addresses than our whitelist
    mock_lb.return_value = [
        _make_lb_entry("0xLeader1", name="Big Fish", alltime_pnl=5_000_000),
    ]
    whitelisted = ["0xCurated1"]
    mock_pos.side_effect = [
        [_make_positions("0xCurated1", account_val=80_000)],
        [],  # no rekt positions
    ]

    with patch("whale_signals.WHALE_WALLET_WHITELIST", whitelisted):
        smart, rekt = fetch_cohorts(n=10)

    assert len(smart) == 1
    w = smart[0]
    assert w["address"] == "0xCurated1"
    assert w["display_name"] == "Anonymous"  # fallback
    assert w["pnl_alltime"] == 0.0
    assert w["pnl_month"] == 0.0
    assert w["account_value"] == 80_000  # from positions call, not leaderboard


# ─── Whitelist mode inactive (regression on U.3 behavior) ──────────────────

@patch("whale_signals.get_bulk_positions")
@patch("whale_signals.get_leaderboard")
def test_whitelist_empty_falls_back_to_u3_composite_sort(mock_lb, mock_pos):
    """When WHALE_WALLET_WHITELIST=[], use the U.3 composite-sorted leaderboard."""
    from whale_signals import fetch_cohorts
    # Two wallets: spike-bias winner vs steady winner. U.3 should rank the
    # steady winner higher; smart_pool order reflects that.
    mock_lb.return_value = [
        _make_lb_entry("0xSpike",  alltime_pnl=10_000_000, month_pnl=5_000,
                        account_val=500_000),
        _make_lb_entry("0xSteady", alltime_pnl=500_000,    month_pnl=200_000,
                        account_val=500_000),
    ]
    # Match both — positions returned in whatever order
    mock_pos.side_effect = [
        [_make_positions("0xSteady"), _make_positions("0xSpike")],
        [],
    ]

    with patch("whale_signals.WHALE_WALLET_WHITELIST", []):
        smart, rekt = fetch_cohorts(n=2)

    smart_call = mock_pos.call_args_list[0]
    smart_pool_arg = smart_call.args[0]
    # U.3 composite sort → steady should appear BEFORE spike in the pool
    assert smart_pool_arg.index("0xSteady") < smart_pool_arg.index("0xSpike")
