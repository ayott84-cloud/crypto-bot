"""Aug 14 2026 — live fills could not reach the backtest's fill.

First live entry:  [rev] OPEN QQQ x2 @ 730.70 (signal 723.70)
About 1% between the price the sleeve decided on and the price it paid,
on a sleeve whose entire edge is a small snap-back. The trade closed
+$0.29 on a $1,461 position while the divergence against it was twenty
times the gain.

Two structural mismatches, not noise:

  1. WRONG DECISION BAR. replay_stock_rev builds window = df.iloc[:i+1]
     and the sleeve reads iloc[-2], so it decides on bar i-1 and fills
     at bar i's close. Live, an end-of-day vendor has published only
     through YESTERDAY, so the frame's last row is D-1 and iloc[-2]
     lands on D-2 — one session older than the replay's decision bar.
     The fill bar (today) is simply absent from the frame.

  2. WRONG TIME OF DAY. The replay fills at bar i's CLOSE. The daemon
     polls on a fixed interval, and once the per-bar cadence gate landed
     it acts on the FIRST poll after the open — the furthest point in
     the session from the close.

Fix for (1): append today's session to the live frame at the current
price. iloc[-2] then lands on D-1 exactly as in the replay, and the fill
corresponds to bar D. Because every sleeve reads iloc[-2], and a
trailing rolling window evaluated at -2 spans [-201:-1], the appended
row never enters an indicator it should not.

Fix for (2): run the entry pass only inside the closing window.

Run: python -m pytest tests/test_stock_fill_alignment.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

import market_calendar as mc
import stock_daily_main as sdm


def _rows(n, end_date="2026-08-13"):
    """WEEX 11-column positional rows ending on `end_date`."""
    idx = pd.bdate_range(end=end_date, periods=n)
    out = []
    for i, ts in enumerate(idx):
        ms = int(ts.tz_localize("UTC").timestamp() * 1000)
        v = f"{100.0 + i}"
        out.append([ms, v, v, v, v, "1000000", ms + 86_399_999,
                     "0", "0", "0", "0"])
    return out


# ─── (1) The decision bar ────────────────────────────────────────────────

def test_appended_session_puts_the_decision_bar_on_yesterday():
    """The replay decides on i-1 and fills at i. Live must match."""
    df = sdm.build_dataframe(_rows(250, end_date="2026-08-13"))
    padded = sdm.append_forming_bar(df, price=730.70,
                                      session=pd.Timestamp("2026-08-14"))
    assert str(padded.index[-1].date()) == "2026-08-14"
    assert str(padded.index[-2].date()) == "2026-08-13", \
        "the decision bar is still a session too old"


def test_the_appended_bar_carries_the_live_price():
    df = sdm.build_dataframe(_rows(50, end_date="2026-08-13"))
    padded = sdm.append_forming_bar(df, price=730.70,
                                      session=pd.Timestamp("2026-08-14"))
    assert float(padded["close"].iloc[-1]) == pytest.approx(730.70)


def test_a_trailing_indicator_at_the_decision_bar_ignores_the_new_row():
    """rolling(n).mean().iloc[-2] spans [-n-1:-1], so the appended bar is
    structurally excluded. If that ever stops holding, the sleeve starts
    deciding on a partial session."""
    df = sdm.build_dataframe(_rows(250, end_date="2026-08-13"))
    before = df["close"].rolling(200).mean().iloc[-1]
    padded = sdm.append_forming_bar(df, price=99999.0,
                                      session=pd.Timestamp("2026-08-14"))
    after = padded["close"].rolling(200).mean().iloc[-2]
    assert float(after) == pytest.approx(float(before))


def test_appending_is_a_no_op_when_the_session_is_already_present():
    """After the vendor publishes, the row must not be duplicated."""
    df = sdm.build_dataframe(_rows(50, end_date="2026-08-14"))
    padded = sdm.append_forming_bar(df, price=730.70,
                                      session=pd.Timestamp("2026-08-14"))
    assert len(padded) == len(df)
    assert str(padded.index[-1].date()) == "2026-08-14"


def test_appending_without_a_price_leaves_the_frame_alone():
    """No live price means no honest way to stamp the bar."""
    df = sdm.build_dataframe(_rows(50, end_date="2026-08-13"))
    assert len(sdm.append_forming_bar(df, price=None,
                                        session=pd.Timestamp("2026-08-14"))) \
        == len(df)


# ─── (2) The time of day ─────────────────────────────────────────────────

def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=mc.ET)


def test_closing_window_is_open_just_before_the_bell():
    assert sdm.in_closing_window(_et(2026, 8, 13, 15, 50)) is True


def test_closing_window_is_shut_at_the_open():
    """Where the cadence gate was landing every entry."""
    assert sdm.in_closing_window(_et(2026, 8, 13, 9, 35)) is False


def test_closing_window_is_shut_midday():
    assert sdm.in_closing_window(_et(2026, 8, 13, 12, 0)) is False


def test_closing_window_tracks_a_half_day_close():
    """A window hardcoded to 15:45 would open three hours after a
    half-day close and never fire."""
    assert sdm.in_closing_window(_et(2026, 11, 27, 12, 50)) is True
    assert sdm.in_closing_window(_et(2026, 11, 27, 15, 50)) is False


def test_closing_window_is_shut_when_the_market_is_shut():
    assert sdm.in_closing_window(_et(2026, 8, 15, 15, 50)) is False   # Sat


def test_closing_window_never_raises_on_a_bad_clock():
    assert sdm.in_closing_window(None) in (True, False)


# ─── Wired in ────────────────────────────────────────────────────────────

def test_run_cycle_gates_entries_on_the_closing_window():
    import inspect
    src = inspect.getsource(sdm.run_cycle)
    assert "in_closing_window(" in src, \
        "entries still fire at whatever time the poll lands"


def test_frame_appends_the_forming_bar():
    import inspect
    src = inspect.getsource(sdm._frame)
    assert "append_forming_bar(" in src
