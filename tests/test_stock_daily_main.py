"""Module 2 Phase S3 — the daily stock daemon.

One process runs all three sleeves because all three decide on daily
bars at or near the close. It follows breakout_main's shape so the
shared machinery (position_manager, journal, kill_switch, notifier)
works unchanged.

WHAT IS DIFFERENT FROM EVERY CRYPTO DAEMON, and why each matters:

  * MARKET HOURS. The crypto loop is `while True: run_cycle(); sleep()`.
    Run that against a market shut 17 hours a day and you burn API
    quota on identical bars all night and log stale signal_status
    entries that then pollute fleet_review's blocker histogram. The
    cycle short-circuits when the market is closed.

  * REBALANCE CADENCE. Trend and dual are MONTH-END strategies; the
    reversion sleeve is daily. Running the monthly sleeves every day
    would multiply their trade count — and their costs — by ~21.

  * LONG/FLAT. No short branch exists to get wrong.

Run: python -m pytest tests/test_stock_daily_main.py -v
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

import stock_daily_main as sdm

ET = ZoneInfo("America/New_York")

# Captured before the autouse fixture stubs it out, so the cadence test
# can exercise the real calendar logic.
_REAL_IS_REBALANCE_DAY = sdm._is_rebalance_day


class _FakeExec:
    def __init__(self, price=100.0, bars=400):
        self.calls = []
        self._price = price
        self._bars = bars

    def get_klines(self, symbol, interval="1d", limit=300):
        n = self._bars
        base = 1_600_000_000_000
        closes = np.linspace(80.0, self._price, n)
        return [[base + i * 86_400_000, str(c), str(c * 1.01), str(c * 0.99),
                  str(c), "1000000", base + (i + 1) * 86_400_000 - 1,
                  "0", "0", "0", "0"] for i, c in enumerate(closes)]

    def get_symbol_price(self, symbol):
        return self._price

    def get_account_balance(self):
        return {"balance": 10000.0}

    def open_long(self, symbol, qty, **kw):
        self.calls.append(("BUY", symbol, qty))
        return {"ok": True}

    def close_long_full(self, symbol):
        self.calls.append(("CLOSE", symbol))
        return {"ok": True}

    def cancel_pending_orders(self, symbol):
        return {"ok": True}

    def get_qty_step(self, s=None): return 1.0
    def get_min_qty(self, s=None): return 1.0
    def get_tick_size(self, s=None): return 0.01
    def load_contract_info(self, s): return None


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(sdm, "_HEARTBEAT_FILE", tmp_path / ".stock_heartbeat")
    monkeypatch.setattr(sdm, "STOCK_PAUSED", True)
    # default: market open, mid-month (so monthly sleeves stay quiet)
    monkeypatch.setattr(sdm.mc, "is_market_open", lambda ts=None: True)
    monkeypatch.setattr(sdm, "_is_rebalance_day", lambda *a, **kw: False)


# ─── Heartbeat comes first, always ───────────────────────────────────────

def test_heartbeat_written_before_the_pause_return(tmp_path, monkeypatch):
    """A paused bot that stops beating looks DEAD to the sentinel. The
    crypto module fixed this once already (whale, Phase A.1)."""
    hb = tmp_path / ".stock_heartbeat"
    monkeypatch.setattr(sdm, "_HEARTBEAT_FILE", hb)
    sdm.run_cycle(_FakeExec(), {"positions": {}})
    assert hb.exists()


def test_heartbeat_written_even_when_market_closed(tmp_path, monkeypatch):
    """Closed != dead. The bot is alive and waiting; the sentinel must
    be able to tell the difference."""
    hb = tmp_path / ".stock_heartbeat"
    monkeypatch.setattr(sdm, "_HEARTBEAT_FILE", hb)
    monkeypatch.setattr(sdm.mc, "is_market_open", lambda ts=None: False)
    sdm.run_cycle(_FakeExec(), {"positions": {}})
    assert hb.exists()


# ─── Market hours gate ───────────────────────────────────────────────────

def test_no_entries_when_market_closed(monkeypatch):
    monkeypatch.setattr(sdm, "STOCK_PAUSED", False)
    monkeypatch.setattr(sdm.mc, "is_market_open", lambda ts=None: False)
    monkeypatch.setattr(sdm, "_is_rebalance_day", lambda *a, **kw: True)
    ex = _FakeExec()
    sdm.run_cycle(ex, {"positions": {}})
    assert not any(c[0] == "BUY" for c in ex.calls), \
        "traded into a closed market"


def test_sleep_seconds_waits_for_the_open_when_closed(monkeypatch):
    """Polling every 5 minutes against a shut market burns quota and
    logs stale signal_status all night."""
    monkeypatch.setattr(sdm.mc, "is_market_open", lambda ts=None: False)
    monkeypatch.setattr(sdm.mc, "next_open",
                          lambda ts=None: datetime(2026, 8, 3, 9, 30, tzinfo=ET))
    now = datetime(2026, 8, 3, 4, 0, tzinfo=ET)
    s = sdm.sleep_seconds(now=now)
    assert s > 3600, f"expected a long sleep to the open, got {s}s"


def test_sleep_seconds_is_the_poll_interval_when_open(monkeypatch):
    monkeypatch.setattr(sdm.mc, "is_market_open", lambda ts=None: True)
    assert sdm.sleep_seconds() == sdm.STOCK_POLL_INTERVAL_SECONDS


# ─── Pause + kill switch ─────────────────────────────────────────────────

def test_pause_blocks_entries_but_still_manages_exits(monkeypatch):
    monkeypatch.setattr(sdm, "STOCK_PAUSED", True)
    monkeypatch.setattr(sdm, "_is_rebalance_day", lambda *a, **kw: True)
    ex = _FakeExec()
    state = {"positions": {"STOCK_REV_SPY": {
        "symbol": "SPY", "entry_price": 500.0, "quantity": 10,
        "direction": "LONG", "strategy": "SPY 1D StockRev",
        "sleeve": "rev", "bars_held": 99}}}
    monkeypatch.setattr(sdm, "_exit_reason_for", lambda *a, **kw: "Time Stop")
    sdm.run_cycle(ex, state)
    assert any(c[0] == "CLOSE" for c in ex.calls), "paused bot stopped exiting"
    assert not any(c[0] == "BUY" for c in ex.calls)


def test_unapproved_sleeve_cannot_trade_even_when_unpaused(monkeypatch):
    """The trend sleeve failed S2. It stays defined and replayable, but
    unpausing the daemon must not let it trade — the gate verdict has to
    survive an operator flipping a flag."""
    monkeypatch.setattr(sdm, "should_pause", lambda o: type(
        "S", (), {"paused": False, "reason": ""})())
    assert sdm._sleeve_blocked("trend") is True
    assert sdm._sleeve_blocked("rev") is False
    assert sdm._sleeve_blocked("dual") is False


def test_trend_sleeve_takes_no_entries_on_a_rebalance_day(monkeypatch):
    monkeypatch.setattr(sdm, "STOCK_PAUSED", False)
    monkeypatch.setattr(sdm, "_is_rebalance_day", lambda *a, **kw: True)
    monkeypatch.setattr(sdm, "should_pause", lambda o: type(
        "S", (), {"paused": False, "reason": ""})())
    state = {"positions": {}}
    sdm.run_cycle(_FakeExec(price=500.0), state)
    assert not any(k.startswith("STOCK_TREND_")
                    for k in state["positions"]), "a failed sleeve traded"


def test_kill_switch_blocks_a_single_sleeve(monkeypatch):
    monkeypatch.setattr(sdm, "STOCK_PAUSED", False)
    monkeypatch.setattr(sdm, "_is_rebalance_day", lambda *a, **kw: True)

    class _S:
        def __init__(self, p): self.paused, self.reason = p, "test"

    monkeypatch.setattr(sdm, "should_pause",
                          lambda owner: _S(owner == "stockrev"))
    # Both are S3-approved, so only the kill switch differentiates them.
    assert sdm._sleeve_blocked("rev") is True
    assert sdm._sleeve_blocked("dual") is False


# ─── Rebalance cadence ───────────────────────────────────────────────────

def test_monthly_sleeves_only_act_on_the_rebalance_day(monkeypatch):
    """Running a month-end strategy daily multiplies its trades — and
    its costs — by ~21."""
    monkeypatch.setattr(sdm, "STOCK_PAUSED", False)
    monkeypatch.setattr(sdm, "_is_rebalance_day", lambda *a, **kw: False)
    ex = _FakeExec()
    sdm.run_cycle(ex, {"positions": {}})
    assert not any(c[0] == "BUY" for c in ex.calls)


def test_is_rebalance_day_is_the_last_session_of_the_month(monkeypatch):
    monkeypatch.setattr(sdm, "_is_rebalance_day", _REAL_IS_REBALANCE_DAY)
    # 2026-08-31 is a Monday and the last trading day of August
    assert sdm._is_rebalance_day(date(2026, 8, 31)) is True
    assert sdm._is_rebalance_day(date(2026, 8, 28)) is False
    # month ending on a weekend: 2026-05-31 is a Sunday, so the last
    # SESSION is Fri 05-29 — the reason this is calendar-derived rather
    # than "is it the 31st"
    assert sdm._is_rebalance_day(date(2026, 5, 29)) is True


# ─── Positions are registered with their bracket + sleeve ───────────────

def test_sizing_must_clear_one_whole_share():
    """At $100/trade the daemon could not buy a single share of SPY and
    silently skipped every entry — a strategy that never trades looks
    exactly like one with no signals."""
    from stock_config import STOCK_MARGIN_PER_TRADE
    assert STOCK_MARGIN_PER_TRADE >= 700, \
        "per-trade size cannot buy one share of the most expensive sleeve ETF"


def test_entry_skipped_loudly_when_price_exceeds_size(monkeypatch, caplog):
    monkeypatch.setattr(sdm, "STOCK_MARGIN_PER_TRADE", 100.0)
    state = {"positions": {}}
    with caplog.at_level("INFO"):
        sdm.open_stock_position(_FakeExec(price=500.0), state, "SPY_TREND",
                                  {"symbol": "SPY",
                                   "strategy_name": "SPY 1M StockTrend"},
                                  sleeve="trend", price=500.0)
    assert state["positions"] == {}
    assert any("exceeds per-trade size" in r.message for r in caplog.records)


def test_entry_persists_sleeve_and_bracket_marker(monkeypatch):
    monkeypatch.setattr(sdm, "STOCK_PAUSED", False)
    monkeypatch.setattr(sdm, "_is_rebalance_day", lambda *a, **kw: True)
    monkeypatch.setattr(sdm, "should_pause", lambda o: type(
        "S", (), {"paused": False, "reason": ""})())
    state = {"positions": {}}
    sdm.open_stock_position(_FakeExec(), state, "SPY_TREND",
                              {"symbol": "SPY", "sma_period": 200,
                               "strategy_name": "SPY 1M StockTrend"},
                              sleeve="trend", price=500.0)
    pos = state["positions"].get("STOCK_TREND_SPY_TREND")
    assert pos is not None
    assert pos["direction"] == "LONG"
    assert pos["sleeve"] == "trend"
    assert pos["bracket_kind"] == "sleeve_exit"
    assert pos["signal_price"] == pytest.approx(500.0)


def test_signal_status_is_persisted_for_the_dashboard(monkeypatch):
    monkeypatch.setattr(sdm, "STOCK_PAUSED", False)
    monkeypatch.setattr(sdm, "_is_rebalance_day", lambda *a, **kw: True)
    monkeypatch.setattr(sdm, "should_pause", lambda o: type(
        "S", (), {"paused": False, "reason": ""})())
    state = {"positions": {}}
    sdm.run_cycle(_FakeExec(), state)
    ss = state.get("stock_signal_status") or {}
    assert ss, "no signal_status written — why-silent panel goes blank"
    row = next(iter(ss.values()))
    for k in ("would_enter", "blocked_by", "checked_at", "sleeve"):
        assert k in row


# ─── Fill divergence (the paper-flatters-you metric) ────────────────────

def test_fill_divergence_recorded_on_close(monkeypatch):
    """Alpaca paper models no fees, no slippage and no queue position,
    so it flatters every strategy by roughly our whole cost model.
    Logging signal-vs-fill makes that measurable instead of assumed."""
    d = sdm.fill_divergence_pct(signal_price=100.0, fill_price=100.05)
    assert d == pytest.approx(0.05, abs=1e-6)
    assert sdm.fill_divergence_pct(0, 10) is None
    assert sdm.fill_divergence_pct(None, 10) is None
