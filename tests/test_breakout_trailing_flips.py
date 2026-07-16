"""Jul 16 2026 trailing-exit A/B verdicts → per-asset config flips.

The A/B (tools/ab_breakout_trailing.py, 17000-bar Coinbase windows,
same window replayed through all four arms) exposed that NO live
breakout asset had use_trailing_exit set (live == no_trail identically
across all 331 trades — the knob only ever existed in the candidate
factory). Verdicts under the pre-registered rule (challenger PF >=
live + 0.10, n >= 5):

  ETH_4H  SWITCH -> early_arm  (PF 1.04->1.54, DD 64.3->22.2, n=71, ~4.9y)
  INJ_1H  SWITCH -> wide_trail (PF 1.05->1.17, +8.7%->+30.8%, n=79, ~1.9y)
  ETH_1H  KEEP (early_arm PF 0.91 — tight trails whipsaw on 1h noise)
  DOGE_1H KEEP (wide_trail +0.10 short of... -0.03 — under epsilon)
  SOL_4H  HOLD — verdict said wide_trail but the window was TRUNCATED
          to 1.4y (pre retry-fix); rerun before flipping.

Run: python -m pytest tests/test_breakout_trailing_flips.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

from breakout_config import BREAKOUT_ASSETS, BREAKOUT_BACKTEST_STATS


def test_eth_4h_switched_to_early_arm_trailing():
    cfg = BREAKOUT_ASSETS["ETH_4H"]
    assert cfg.get("use_trailing_exit") is True
    assert cfg.get("trail_arm_atr_mult") == 1.0
    assert cfg.get("trail_atr_mult") == 1.0


def test_inj_1h_switched_to_wide_trailing():
    cfg = BREAKOUT_ASSETS["INJ_1H"]
    assert cfg.get("use_trailing_exit") is True
    assert cfg.get("trail_arm_atr_mult") == 1.5
    assert cfg.get("trail_atr_mult") == 2.0


def test_keep_verdicts_stay_trail_free():
    """ETH_1H and DOGE_1H keep the Donchian/ADX/SL stack — early-armed
    trails actively destroyed both on 1h (PF 0.91 / 0.89)."""
    for key in ("ETH_1H", "DOGE_1H"):
        assert not BREAKOUT_ASSETS[key].get("use_trailing_exit"), key


def test_sol_4h_held_pending_clean_window():
    """SOL_4H's A/B window was truncated to 1.4y — no flip until the
    rerun on the retry-hardened fetcher confirms."""
    assert not BREAKOUT_ASSETS["SOL_4H"].get("use_trailing_exit")


def test_stats_rows_updated_for_flipped_assets():
    """The projection table must describe the DEPLOYED exit stack —
    flipped assets carry the A/B winning-arm numbers."""
    eth = BREAKOUT_BACKTEST_STATS["ETH_4H"]
    assert eth["pf"] == 1.54
    assert eth["trades"] == 71
    assert eth["dd_pct"] == 22.2
    assert "A/B" in eth["source"]
    inj = BREAKOUT_BACKTEST_STATS["INJ_1H"]
    assert inj["pf"] == 1.17
    assert inj["trades"] == 79
    assert "A/B" in inj["source"]
