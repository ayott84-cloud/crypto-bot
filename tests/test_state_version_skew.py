"""Aug 22 2026 — a new state key was eaten by bots running older code.

stock_last_bar (the churn guard added Aug 14) was written correctly by
the stock daemon, saved correctly, and was ABSENT from state.json days
later. The key was registered in _STOCK_TOPLEVEL and that registration
was deployed.

The cause is version skew. Bots are long-running processes: they import
position_manager once at startup and hold _TOPLEVEL_BY_BOT in memory for
days. _merge_state preserved other owners' top-level keys from a
WHITELIST, so a bot started before the registration had never heard of
stock_last_bar — and since load_state also runs once at startup, the key
was in neither `ours` nor the whitelist. Every save by that bot dropped
it.

The general defect: adding any top-level state key silently required
restarting every bot in the fleet, and skipping one caused quiet data
loss rather than an error. That is the config/deploy-drift class this
project keeps finding, expressed in shared state.

The fix inverts the rule. Instead of preserving keys we recognize, we
preserve everything we do not OWN. A key belonging to a newer version of
another bot is then safe by default.

Run: python -m pytest tests/test_state_version_skew.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pytest

import position_manager as pm


@pytest.fixture
def stale_stock_owner(monkeypatch):
    """Simulate a bot whose in-memory constants predate a new key."""
    tables = dict(pm._TOPLEVEL_BY_BOT)
    tables["stock"] = {"stock_cooldowns", "stock_signal_status"}
    monkeypatch.setattr(pm, "_TOPLEVEL_BY_BOT", tables)


# ─── The regression ──────────────────────────────────────────────────────

def test_an_unknown_key_survives_a_save_by_stale_code(stale_stock_owner):
    """The exact loss: stock_last_bar written Aug 19, gone by Aug 22."""
    disk = {"positions": {}, "stock_last_bar": {"QQQ_REV": "2026-08-17"}}
    out = pm._merge_state({"positions": {}}, disk, owner="momentum")
    assert out.get("stock_last_bar") == {"QQQ_REV": "2026-08-17"}


def test_known_other_owner_keys_still_survive(stale_stock_owner):
    disk = {"positions": {}, "stock_signal_status": {"QQQ_REV": {"x": 1}}}
    out = pm._merge_state({"positions": {}}, disk, owner="momentum")
    assert out["stock_signal_status"] == {"QQQ_REV": {"x": 1}}


def test_a_key_added_by_a_future_bot_is_preserved():
    """Not just stock: any owner's future key must be safe by default."""
    disk = {"positions": {}, "forex_session_state": {"EURUSD": "open"}}
    out = pm._merge_state({"positions": {}}, disk, owner="momentum")
    assert out["forex_session_state"] == {"EURUSD": "open"}


# ─── Ownership still wins for the saver ──────────────────────────────────

def test_the_owner_still_wins_on_its_own_keys():
    ours = {"positions": {}, "signal_status": {"BTC": "fresh"}}
    disk = {"positions": {}, "signal_status": {"BTC": "stale"}}
    out = pm._merge_state(ours, disk, owner="momentum")
    assert out["signal_status"] == {"BTC": "fresh"}


def test_an_owner_can_still_clear_its_own_key():
    """Preserving everything unowned must not resurrect a key its owner
    deliberately emptied."""
    ours = {"positions": {}, "signal_status": {}}
    disk = {"positions": {}, "signal_status": {"BTC": "old"}}
    out = pm._merge_state(ours, disk, owner="momentum")
    assert out["signal_status"] == {}


def test_stock_owner_still_wins_on_stock_keys():
    ours = {"positions": {}, "stock_last_bar": {"QQQ_REV": "2026-08-21"}}
    disk = {"positions": {}, "stock_last_bar": {"QQQ_REV": "2026-08-17"}}
    out = pm._merge_state(ours, disk, owner="stock")
    assert out["stock_last_bar"] == {"QQQ_REV": "2026-08-21"}


# ─── Positions are unaffected ────────────────────────────────────────────

def test_position_ownership_is_unchanged():
    ours = {"positions": {"BTC_4H": {"e": 1}}}
    disk = {"positions": {"WHALE_ETH": {"e": 2}}}
    out = pm._merge_state(ours, disk, owner="momentum")
    assert set(out["positions"]) == {"BTC_4H", "WHALE_ETH"}


def test_positions_never_leak_into_the_toplevel_pass():
    """`positions` is merged by its own rules; the key-preserving pass
    must not overwrite that result with the disk copy."""
    ours = {"positions": {"BTC_4H": {"e": 1}}}
    disk = {"positions": {"BTC_4H": {"e": 999}}}
    out = pm._merge_state(ours, disk, owner="momentum")
    assert out["positions"]["BTC_4H"]["e"] == 1


# ─── The real fleet shape ────────────────────────────────────────────────

def test_a_full_round_trip_across_two_owners_loses_nothing():
    """Modelled the way the daemons actually run: load_state hands each
    bot the whole file, the bot mutates its own keys, then saves.

    Passing a bare {"positions": {}} as `ours` would model a bot that
    LOST its own keys in memory, and clearing them is then the correct
    result — which is a different test (see the deliberate-clear case).
    """
    disk = {
        "positions": {},
        "signal_status": {"BTC": 1},
        "stock_signal_status": {"QQQ_REV": 1},
        "stock_last_bar": {"QQQ_REV": "2026-08-17"},
        "whale_cooldowns": {"HYPE": 1},
        "some_future_key": {"a": 1},
    }
    stock_ours = dict(disk)                      # what load_state returns
    after_stock = pm._merge_state(stock_ours, disk, owner="stock")

    momentum_ours = dict(after_stock)
    momentum_ours["signal_status"] = {"BTC": 2}  # its own key, updated
    after_momentum = pm._merge_state(momentum_ours, after_stock,
                                       owner="momentum")

    for k in disk:
        assert k in after_momentum, f"{k} lost in a two-owner round trip"
    assert after_momentum["signal_status"] == {"BTC": 2}
    assert after_momentum["stock_last_bar"] == {"QQQ_REV": "2026-08-17"}


def test_a_bot_running_stale_code_no_longer_eats_the_new_key(
        stale_stock_owner):
    """The Aug 19-to-22 loss, end to end: the stock daemon writes the
    marker, then a crypto bot whose constants predate it saves twice."""
    disk = {"positions": {}, "stock_last_bar": {"QQQ_REV": "2026-08-17"}}
    ours = {"positions": {}}                     # started before the key
    for _ in range(2):
        disk = pm._merge_state(ours, disk, owner="momentum")
    assert disk["stock_last_bar"] == {"QQQ_REV": "2026-08-17"}
