"""Jul 30 2026 as-of A/B — the trailing round's honest conclusion.

The Jul 16-17 flips (ETH_4H + SOL_4H early_arm, INJ_1H wide) were made
on replays with the static-gate bug (run-day 1D EMA state applied to
all historical bars). The Jul 30 rerun on the as-of harness inverted
every verdict:

  ETH_4H  no_trail 1.87/+264.9%/DD31.9  vs deployed early_arm 1.32
          -> REVERT to the trail-free stack (dominant on PF and DD)
  SOL_4H  no_trail 2.11/+293.8%/DD56.4  vs deployed 1.69
          -> REVERT per the pre-registered PF rule (wide 1.93/DD27.9
             noted as the Calmar alternative)
  INJ_1H  ALL arms < 1.0 (best 0.84) — the ASSET fails, not the exit
          stack -> DEMOTED to candidates

Run: python -m pytest tests/test_breakout_trailing_flips.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

from breakout_config import (
    BREAKOUT_ASSETS, BREAKOUT_CANDIDATE_ASSETS, BREAKOUT_BACKTEST_STATS,
)


def test_eth_4h_reverted_to_trail_free():
    cfg = BREAKOUT_ASSETS["ETH_4H"]
    assert not cfg.get("use_trailing_exit")
    assert "trail_arm_atr_mult" not in cfg
    assert "trail_atr_mult" not in cfg


def test_sol_4h_reverted_to_trail_free():
    cfg = BREAKOUT_ASSETS["SOL_4H"]
    assert not cfg.get("use_trailing_exit")
    assert "trail_arm_atr_mult" not in cfg
    assert "trail_atr_mult" not in cfg


def test_inj_1h_demoted_to_candidates():
    """All four exit arms below PF 1.0 on the honest window — same
    evidence class as the Jul 4 Step-2 demotions. Candidate dict keeps
    exit management alive for any orphan position."""
    assert "INJ_1H" not in BREAKOUT_ASSETS
    assert "INJ_1H" in BREAKOUT_CANDIDATE_ASSETS


def test_keep_assets_stay_trail_free():
    for key in ("ETH_1H", "DOGE_1H"):
        assert not BREAKOUT_ASSETS[key].get("use_trailing_exit"), key


def test_stats_rows_carry_asof_numbers():
    """Projection table must describe the DEPLOYED (trail-free) stacks
    measured on the as-of harness."""
    eth = BREAKOUT_BACKTEST_STATS["ETH_4H"]
    assert eth["pf"] == 1.87
    assert eth["trades"] == 107
    assert "as-of" in eth["source"]
    sol = BREAKOUT_BACKTEST_STATS["SOL_4H"]
    assert sol["pf"] == 2.11
    assert sol["trades"] == 67
    doge = BREAKOUT_BACKTEST_STATS["DOGE_1H"]
    assert doge["pf"] == 1.83
    inj = BREAKOUT_BACKTEST_STATS["INJ_1H"]
    assert inj["pf"] == 0.84
    assert "DEMOTED" in inj["source"]
