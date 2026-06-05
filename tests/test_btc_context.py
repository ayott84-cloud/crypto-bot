"""Unit tests for main._btc_context_keys + _build_btc_context.

Phase B.1 of the comprehensive enhancement plan. The previous code at
main.py:200 hardcoded btc_ema_period = 50 regardless of each asset's
config. ADA_4H, DOT_1D, SHIB_1D all specify their own btc_ema_period
(100, 200, 100) but those values were silently ignored. The fix keys
the BTC-context cache by (interval, ema_period) so each combination
gets its own correctly-computed BTC EMA.

This test file splits the verification:
  - `_btc_context_keys` is a pure function on the ASSETS dict — fully
    unit-testable here (no pandas/pandas_ta dependency).
  - `_build_btc_context` accepts a `compute_ema` injection so the
    integration with pandas_ta is decoupled — tests inject a fake.

Run: python -m pytest tests/test_btc_context.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

# Direct import path stub: signals.py pulls in pandas at module-load. The
# tested helpers live in btc_context.py (Phase B.1) — a small module so
# pandas-free tests can run without main.py's heavy imports.
from btc_context import _btc_context_keys, _build_btc_context


# ─── _btc_context_keys (pure) ───────────────────────────────────────────────

def test_keys_collapse_duplicates_across_assets():
    """Multiple assets with the same (interval, ema_period) collapse to one entry."""
    assets = {
        "A_4H": {"interval": "4H", "use_btc_filter": True, "btc_ema_period": 50},
        "B_4H": {"interval": "4H", "use_btc_filter": True, "btc_ema_period": 50},  # dup
        "C_1D": {"interval": "1D", "use_btc_filter": True, "btc_ema_period": 50},
    }
    assert _btc_context_keys(assets) == {("4H", 50), ("1D", 50)}


def test_keys_separate_when_same_interval_different_ema_period():
    """The whole point of B.1 — different periods on same TF must be distinct."""
    assets = {
        "ADA_4H":  {"interval": "4H", "use_btc_filter": True, "btc_ema_period": 100},
        "BTC_4H":  {"interval": "4H", "use_btc_filter": True, "btc_ema_period": 50},
        "DOT_1D":  {"interval": "1D", "use_btc_filter": True, "btc_ema_period": 200},
    }
    assert _btc_context_keys(assets) == {("4H", 100), ("4H", 50), ("1D", 200)}


def test_keys_exclude_assets_without_btc_filter():
    assets = {
        "WITH":    {"interval": "4H", "use_btc_filter": True,  "btc_ema_period": 50},
        "WITHOUT": {"interval": "4H", "use_btc_filter": False, "btc_ema_period": 50},
    }
    assert _btc_context_keys(assets) == {("4H", 50)}


def test_keys_default_ema_period_to_50_when_missing():
    assets = {"X_4H": {"interval": "4H", "use_btc_filter": True}}  # no btc_ema_period
    assert _btc_context_keys(assets) == {("4H", 50)}


def test_keys_empty_when_no_assets_use_btc_filter():
    assets = {"X": {"interval": "4H", "use_btc_filter": False}}
    assert _btc_context_keys(assets) == set()


# ─── _build_btc_context (injected EMA computer) ─────────────────────────────

class FakeExecutor:
    """Returns synthetic klines (length controls the data-sufficiency branch)."""
    def __init__(self, n_bars: int = 200):
        self.n_bars = n_bars
        self.calls = []

    def get_klines(self, symbol, interval, limit):
        self.calls.append((symbol, interval, limit))
        if self.n_bars <= 0:
            return []
        # [open_time, o, h, l, c, v] — c rises linearly
        return [
            [i * 60_000, "80000", "80100", "79900", str(80000 + i * 10.0), "1"]
            for i in range(self.n_bars)
        ]


def _fake_compute_ema(klines, period: int) -> tuple[float, float]:
    """Stand-in for the pandas_ta EMA call. Returns (last_close, period*1.0).

    Using period as the EMA value gives us a verifiable per-period output
    without needing pandas_ta installed in the test env.
    """
    last_close = float(klines[-1][4])
    return last_close, float(period)


def test_build_btc_context_returns_one_entry_per_unique_combo():
    assets = {
        "A_4H": {"interval": "4H", "use_btc_filter": True, "btc_ema_period": 50},
        "B_4H": {"interval": "4H", "use_btc_filter": True, "btc_ema_period": 100},
        "C_1D": {"interval": "1D", "use_btc_filter": True, "btc_ema_period": 50},
    }
    ctx = _build_btc_context(FakeExecutor(), assets, compute_ema=_fake_compute_ema)
    assert set(ctx.keys()) == {("4H", 50), ("4H", 100), ("1D", 50)}


def test_build_btc_context_uses_per_combo_ema_period():
    """The injected compute_ema must be called with the right period per key."""
    assets = {
        "A_4H": {"interval": "4H", "use_btc_filter": True, "btc_ema_period": 50},
        "B_4H": {"interval": "4H", "use_btc_filter": True, "btc_ema_period": 100},
    }
    ctx = _build_btc_context(FakeExecutor(), assets, compute_ema=_fake_compute_ema)
    # Our fake returns the period as the ema value
    assert ctx[("4H", 50)][1] == 50.0
    assert ctx[("4H", 100)][1] == 100.0
    # Close should be the last-bar close (same for both keys)
    assert ctx[("4H", 50)][0] == ctx[("4H", 100)][0]


def test_build_btc_context_returns_none_pair_when_klines_too_short():
    assets = {"X_4H": {"interval": "4H", "use_btc_filter": True, "btc_ema_period": 50}}
    ctx = _build_btc_context(FakeExecutor(n_bars=30), assets, compute_ema=_fake_compute_ema)
    assert ctx[("4H", 50)] == (None, None)


def test_build_btc_context_returns_none_pair_on_empty_klines():
    assets = {"X_4H": {"interval": "4H", "use_btc_filter": True, "btc_ema_period": 50}}
    ctx = _build_btc_context(FakeExecutor(n_bars=0), assets, compute_ema=_fake_compute_ema)
    assert ctx[("4H", 50)] == (None, None)


def test_build_btc_context_fetches_klines_once_per_unique_interval():
    """Two assets on same interval+period combo must not double-fetch BTC klines."""
    assets = {
        "A_4H": {"interval": "4H", "use_btc_filter": True, "btc_ema_period": 50},
        "B_4H": {"interval": "4H", "use_btc_filter": True, "btc_ema_period": 50},  # dup
        "C_4H": {"interval": "4H", "use_btc_filter": True, "btc_ema_period": 100},
    }
    fake = FakeExecutor()
    _build_btc_context(fake, assets, compute_ema=_fake_compute_ema)
    # Two unique combos → at most 2 get_klines calls
    # (an even smarter impl could fetch once per interval and reuse — but
    # 2 is the contract this test enforces, matching the dict-keying.)
    assert len(fake.calls) <= 2
