"""P4 Step-2 universe cut (Jul 4 2026, operator-approved).

Long-window honest replays (2yr 1h / ~5-8yr 4h / 1yr 15m, Coinbase,
costs + deployed exits) reduced the fleet:

  scalp    KEEP  BTC_5M 1.18 / ETH_5M 1.54 / XRP_5M 1.25
           DEMOTE DOGE_5M 0.41, LINK_5M 0.76
  breakout KEEP  SOL_4H 1.43 / ETH_4H 1.25 / DOGE_1H 1.41 /
                 ETH_1H 1.24 / INJ_1H 1.23
           DEMOTE BTC_4H 1.16(DD58%), BTC_1H 0.85, ADA_1H 0.75,
                 NEAR_1H 1.07(DD63%), AAVE_1H 0.88, BNB/TRX (no
                 long-window data source)

Also: demoting an asset with an OPEN position must not orphan it — the
exit-management loops fall back to the candidate dicts for cfg lookup.

Run: python -m pytest tests/test_step2_universe_cut.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")


# ─── Scalp universe ────────────────────────────────────────────────────────

def test_scalp_live_set_is_step2_survivors():
    from scalp_config import SCALP_ASSETS
    assert set(SCALP_ASSETS) == {"BTC_5M", "ETH_5M", "XRP_5M"}


def test_scalp_demotions_are_candidates():
    from scalp_config import SCALP_CANDIDATE_ASSETS
    for k in ("DOGE_5M", "LINK_5M"):
        assert k in SCALP_CANDIDATE_ASSETS, k


def test_scalp_stats_are_honest_long_window():
    """The projection table must read the 1-year honest replay numbers,
    not the retired M.2 liberal-fill fantasy (PF 6.02/7.98/999)."""
    from scalp_config import SCALP_BACKTEST_STATS
    assert SCALP_BACKTEST_STATS["ETH_5M"]["pf"] == pytest.approx(1.54)
    assert SCALP_BACKTEST_STATS["BTC_5M"]["pf"] == pytest.approx(1.18)
    assert SCALP_BACKTEST_STATS["XRP_5M"]["pf"] == pytest.approx(1.25)
    for row in SCALP_BACKTEST_STATS.values():
        assert row["pf"] < 10, "liberal-fill sentinel values must be gone"
        assert 0.5 <= row["years"] <= 1.5


# ─── Breakout universe ─────────────────────────────────────────────────────

def test_breakout_live_set_is_step2_survivors():
    from breakout_config import BREAKOUT_ASSETS
    assert set(BREAKOUT_ASSETS) == {"SOL_4H", "ETH_4H", "DOGE_1H",
                                       "ETH_1H", "INJ_1H"}


def test_breakout_demotions_are_candidates():
    from breakout_config import BREAKOUT_CANDIDATE_ASSETS
    for k in ("BTC_4H", "BTC_1H", "ADA_1H", "NEAR_1H", "AAVE_1H",
                "BNB_4H_D20", "TRX_4H_D20", "BNB_1H_D20"):
        assert k in BREAKOUT_CANDIDATE_ASSETS, k


def test_breakout_stats_are_honest_long_window():
    from breakout_config import BREAKOUT_BACKTEST_STATS
    # SOL_4H superseded Jul 17 2026: the trailing A/B flipped its exit
    # stack to early_arm, and the stats row must describe the DEPLOYED
    # stack (still an honest long-window replay — 11132 bars, ~5.1y).
    assert BREAKOUT_BACKTEST_STATS["SOL_4H"]["pf"] == pytest.approx(1.90)
    assert BREAKOUT_BACKTEST_STATS["ETH_1H"]["pf"] == pytest.approx(1.24)
    for k in ("SOL_4H", "ETH_4H", "DOGE_1H", "ETH_1H", "INJ_1H"):
        assert BREAKOUT_BACKTEST_STATS[k]["years"] >= 1.5, k


# ─── Orphan guards — open positions on demoted assets stay managed ─────────

def test_scalp_exit_cfg_falls_back_to_candidates():
    from scalp_main import _cfg_for_open_position
    assert _cfg_for_open_position("BTC_5M") is not None      # live
    assert _cfg_for_open_position("DOGE_5M") is not None     # demoted
    assert _cfg_for_open_position("NOPE_5M") is None


def test_breakout_exit_cfg_falls_back_to_candidates():
    from breakout_main import _cfg_for_open_position
    assert _cfg_for_open_position("SOL_4H") is not None      # live
    assert _cfg_for_open_position("BTC_1H") is not None      # demoted
    assert _cfg_for_open_position("NOPE_1H") is None


def test_crossover_exit_cfg_falls_back_to_candidates():
    from crossover_main import _cfg_for_open_position
    assert _cfg_for_open_position("ETH_1H") is not None
    assert _cfg_for_open_position("NOPE_1H") is None
