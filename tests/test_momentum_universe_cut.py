"""Momentum Step-2 universe cut (Jul 4 2026, operator-approved).

Multi-year honest replay verdicts (Coinbase, conservative fills, costs):
  KEEP (clean):       BTC 1.57, ADA_4H 1.78, ARB_4H 1.81, INJ_4H 1.45,
                      SUI_1D 2.40, NEAR_1D 1.69
  KEEP (observation): DOGE_4H 1.59, RENDER_4H 1.49, HBAR_4H 1.45,
                      AAVE_4H 1.33 — PF passes, DD 18-23% fails the bar
  DEMOTE (19):        everything else, incl. BTC_1D 0.69, ETH 0.78,
                      LINK_4H 0.79/DD67%, FIL_4H 0.46; ETH_1D/SOL/
                      SHIB_1D are promising-but-undersampled candidates.

Plus P5a parity: positions persist sl_price at entry, and SL/BE exits
journal at the TRIGGER price (the exchange-resident stop), not the
polled close.

Run: python -m pytest tests/test_momentum_universe_cut.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

_KEEP = {"BTC", "ADA_4H", "ARB_4H", "INJ_4H", "SUI_1D", "NEAR_1D",
          "DOGE_4H", "RENDER_4H", "HBAR_4H", "AAVE_4H"}
_DEMOTED = {"BTC_1D", "ETH_1D", "ETH", "XRP", "XRP_4H", "SOL", "HBAR_1D",
             "ADA_1D", "DOT_4H", "DOT_1D", "DOGE_1D", "SHIB_4H", "SHIB_1D",
             "TRX_4H", "AVAX_4H", "LINK_4H", "FIL_4H", "APT_4H", "SUI_4H"}


def test_momentum_live_set_is_step2_survivors():
    from config import ASSETS
    assert set(ASSETS) == _KEEP


def test_momentum_demotions_are_candidates():
    from config import MOMENTUM_CANDIDATE_ASSETS, ASSETS
    for k in _DEMOTED:
        assert k in MOMENTUM_CANDIDATE_ASSETS, k
    assert not set(MOMENTUM_CANDIDATE_ASSETS) & set(ASSETS)


def test_iteration_universe_exit_manages_demoted_open_positions():
    """A demoted asset with an OPEN position stays in the cycle loop
    (exit management) but can never be an entry candidate."""
    from main import _iteration_universe, _may_enter
    assets = {"BTC": {"symbol": "BTCUSDT"}}
    candidates = {"LINK_4H": {"symbol": "LINKUSDT"}}
    # open position on the demoted asset → included for exit management
    uni = _iteration_universe(assets, candidates, open_keys={"LINK_4H"})
    assert set(uni) == {"BTC", "LINK_4H"}
    # no open position → not iterated at all
    uni2 = _iteration_universe(assets, candidates, open_keys=set())
    assert set(uni2) == {"BTC"}
    # entries only ever fire for live-set assets
    assert _may_enter("BTC", assets) is True
    assert _may_enter("LINK_4H", assets) is False


def test_momentum_fill_price_uses_trigger_for_stops():
    """SL/BE full exits journal at the exchange-resident trigger, not
    the polled close (P1.1 exit-price-override parity). Bot-side exits
    (TP2 market close, Stale) keep the polled price."""
    from main import _momentum_fill_price
    lv = {"sl": 99.0, "sl_reason": "SL Hit", "tp1": 103.0, "tp2": 106.0}
    assert _momentum_fill_price("SL Hit", 97.8, lv) == pytest.approx(99.0)
    assert _momentum_fill_price("BE Hit", 99.4, lv) == pytest.approx(99.0)
    assert _momentum_fill_price("TP2 Hit", 106.4, lv) == pytest.approx(106.4)
    assert _momentum_fill_price("Stale Exit", 100.2, lv) == pytest.approx(100.2)
