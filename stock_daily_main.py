"""Module 2 Phase S3 — daily stock sleeve daemon.

ONE process, THREE sleeves. All three decide on daily bars at or near
the close, so they share a daemon (and one position_manager owner,
"stock"). Per-sleeve identity lives on the strategy tag, so each still
gets its own journal rows, kill switch and gate step.

Follows breakout_main's shape deliberately — heartbeat first, exits
before entries, pause check, capacity, save_state — so the shared
machinery keeps working and anyone who knows the crypto bots can read
this one.

THREE THINGS NO CRYPTO DAEMON HAS TO DO:

  * MARKET HOURS. `while True: run_cycle(); sleep(300)` against a market
    that is shut 17 hours a day burns API quota on identical bars all
    night and writes stale signal_status rows that then pollute
    fleet_review's blocker histogram. The cycle short-circuits when
    closed and sleeps to the next open.

  * REBALANCE CADENCE. Trend and dual are MONTH-END strategies; only
    the reversion sleeve is daily. Running the monthly sleeves every
    session would multiply their trade count, and their costs, by ~21.

  * LONG/FLAT ONLY. No short branch exists to get wrong; the executor
    refuses shorts outright.

Sleeves are PAUSED by default (STOCK_PAUSED=true), per the convention
every crypto bot follows: nothing trades until it has earned it.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

import market_calendar as mc
from journal import log_trade
from kill_switch import should_pause
from position_manager import load_state, save_state, register_entry, register_exit
from signals import build_dataframe
from stock_config import (
    STOCK_PAUSED, STOCK_POLL_INTERVAL_SECONDS, STOCK_STATE_KEY_PREFIX,
    STOCK_HEARTBEAT_FILE, STOCK_MARGIN_PER_TRADE, MAX_STOCK_POSITIONS,
    STOCK_TREND_ASSETS, STOCK_REV_ASSETS, STOCK_DUAL_CONFIG,
    S3_APPROVED_SLEEVES,
)
import stock_signals as ss

logger = logging.getLogger("stock_bot")

_HEARTBEAT_FILE = STOCK_HEARTBEAT_FILE

# Sleeve -> kill_switch owner. The two axes are distinct on purpose:
# position_manager owns "stock" (one daemon, one state namespace) while
# kill_switch tracks each sleeve's own loss streak.
_SLEEVE_OWNER = {"trend": "stocktrend", "dual": "stockdual", "rev": "stockrev"}


def _write_heartbeat() -> None:
    """Touch FIRST in run_cycle, before any early return.

    A paused or waiting bot that stops beating looks DEAD to the risk
    sentinel — the crypto module already fixed this once (whale, Phase
    A.1) and again when momentum turned out to have no heartbeat at all.
    """
    try:
        _HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _HEARTBEAT_FILE.touch()
    except Exception as e:  # noqa: BLE001
        logger.warning("heartbeat write failed: %s", e)


def _is_rebalance_day(d=None) -> bool:
    """True on the LAST trading session of the month.

    Month-end is the canonical decision point for both Faber trend and
    Antonacci dual momentum. Deriving it from the calendar (rather than
    'is it the 31st') is what makes it correct in a month ending on a
    weekend or a holiday.
    """
    import datetime as _dt
    d = d or _dt.date.today()
    if not mc.is_trading_day(d):
        return False
    probe = d + _dt.timedelta(days=1)
    for _ in range(10):
        if probe.month != d.month:
            return True
        if mc.is_trading_day(probe):
            return False
        probe += _dt.timedelta(days=1)
    return False


def sleep_seconds(now=None) -> float:
    """Poll interval while open; sleep to the bell when shut.

    Capped so a long weekend still wakes periodically — a daemon that
    sleeps 65 hours straight cannot heartbeat, and a silent heartbeat
    is indistinguishable from a dead process.
    """
    if mc.is_market_open(now):
        return float(STOCK_POLL_INTERVAL_SECONDS)
    try:
        nxt = mc.next_open(now)
        ref = now or datetime.now(tz=mc.ET)
        delta = (nxt - ref).total_seconds()
        return float(max(60.0, min(delta, 3600.0 * 4)))
    except Exception:  # noqa: BLE001
        return float(STOCK_POLL_INTERVAL_SECONDS)


def _sleeve_blocked(sleeve: str) -> bool:
    # Gate first: a sleeve that failed S2 must not be able to trade even
    # if someone unpauses the daemon. The trend sleeve is defined and
    # replayable but NOT approved — this is what makes that stick.
    if sleeve not in S3_APPROVED_SLEEVES:
        return True
    owner = _SLEEVE_OWNER.get(sleeve, "stock")
    try:
        st = should_pause(owner)
    except Exception:  # noqa: BLE001
        return False
    if getattr(st, "paused", False):
        logger.info("[%s] kill switch: %s", sleeve, getattr(st, "reason", ""))
        return True
    return False


def fill_divergence_pct(signal_price, fill_price):
    """Percent difference between the price the signal saw and the price
    we got.

    Alpaca paper matches against real NBBO but models NO fees, NO
    slippage, NO market impact and NO queue position — it flatters every
    strategy by roughly our entire cost model. Logging this makes the
    gap measurable rather than assumed, and it is the Step-5 gate metric
    in the P4 runbook.
    """
    try:
        s, f = float(signal_price), float(fill_price)
    except (TypeError, ValueError):
        return None
    if s == 0:
        return None
    return (f - s) / s * 100.0


def _frame(executor, symbol: str, interval: str, bars: int = 400):
    raw = executor.get_klines(symbol, interval, bars)
    if not raw:
        return None
    df = build_dataframe(raw).dropna(subset=["close"])
    # Today's session is absent until the vendor publishes it after the
    # close. Append it at the live price so iloc[-2] is the same
    # decision bar the replay uses.
    try:
        return append_forming_bar(df, executor.get_symbol_price(symbol))
    except Exception:  # noqa: BLE001
        return df


def _state_key(asset_name: str, sleeve: str) -> str:
    return f"{STOCK_STATE_KEY_PREFIX}{sleeve.upper()}_{asset_name}"


def _count_open(state: dict) -> int:
    return sum(1 for k in (state.get("positions") or {})
                if k.startswith(STOCK_STATE_KEY_PREFIX))


def open_stock_position(executor, state: dict, asset_name: str, cfg: dict,
                          sleeve: str, price: float,
                          bar_id: str | None = None) -> None:
    """Buy whole shares and register the position.

    `bracket_kind="sleeve_exit"` records that the stop is MANAGED BY THE
    DAEMON rather than resting at the venue: Alpaca rejects bracket
    orders outside regular hours, and these sleeves decide near the
    close. Recording it means the risk sentinel can distinguish "no
    venue stop by design" from "the stop failed to place".
    """
    symbol = cfg["symbol"]
    qty = int(STOCK_MARGIN_PER_TRADE // max(price, 0.01))
    if qty < 1:
        logger.info("[%s] %s at %.2f exceeds per-trade size; skipping",
                     sleeve, symbol, price)
        return
    try:
        executor.open_long(symbol, qty)
    except Exception as e:  # noqa: BLE001
        logger.error("[%s] open failed for %s: %s", sleeve, symbol, e)
        return

    fill = executor.get_symbol_price(symbol) or price
    register_entry(
        state, _state_key(asset_name, sleeve),
        entry_price=fill, atr_at_entry=0.0, quantity=qty,
        strategy=cfg.get("strategy_name", f"{symbol} {sleeve}"),
        entry_reason=f"{sleeve} entry", symbol=symbol, direction="LONG",
        sleeve=sleeve,
        bracket_kind="sleeve_exit",
        sl_price=None, tp_price=None,
        signal_price=price,
        fill_divergence_pct=fill_divergence_pct(price, fill),
        entry_bar=bar_id,
    )
    logger.info("[%s] OPEN %s x%d @ %.2f (signal %.2f)",
                 sleeve, symbol, qty, fill, price)


def close_stock_position(executor, state: dict, key: str, reason: str) -> None:
    pos = (state.get("positions") or {}).get(key)
    if not pos:
        return
    symbol = pos.get("symbol", "")
    exit_price = executor.get_symbol_price(symbol) or pos.get("entry_price")
    try:
        executor.close_long_full(symbol)
        executor.cancel_pending_orders(symbol)
    except Exception as e:  # noqa: BLE001
        logger.error("[%s] close failed: %s", key, e)
        return
    register_exit(state, key)
    try:
        log_trade(
            symbol=symbol, direction="LONG",
            entry_price=pos["entry_price"],
            exit_price=exit_price or pos["entry_price"],
            quantity=float(pos.get("quantity") or 0), leverage=1,
            strategy=pos.get("strategy", "StockTrend"),
            entry_reason=pos.get("entry_reason", ""), exit_reason=reason,
            date_closed=datetime.now(timezone.utc),
        )
    except Exception as e:  # noqa: BLE001
        logger.error("[%s] log_trade failed: %s", key, e)
    logger.info("[%s] CLOSE %s — %s", key, symbol, reason)


# ─── Fill alignment (Aug 14 2026) ────────────────────────────────────────
#
# First live entry: OPEN QQQ @ 730.70 on a 723.70 signal — about 1% on a
# sleeve whose whole edge is a small snap-back. Two structural
# mismatches against replay_stock_rev, which decides on bar i-1 and
# fills at bar i's CLOSE.
#
#   1. An end-of-day vendor has published only through yesterday, so the
#      live frame ends at D-1 and iloc[-2] lands on D-2 — a session
#      older than the replay's decision bar. Today, the fill bar, is
#      absent entirely. Appending it at the live price restores the
#      replay's exact shape.
#   2. The daemon polls on a fixed interval, and with the per-bar
#      cadence gate it acted on the first poll after the OPEN — the
#      furthest point in the session from the close it is measured
#      against. Entries now run only in the closing window.

STOCK_CLOSING_WINDOW_MINUTES = 20


def append_forming_bar(df, price, session=None):
    """Add today's in-progress session at the live price.

    Every sleeve reads iloc[-2], and a trailing rolling window evaluated
    at -2 spans [-n-1:-1], so the appended row is structurally excluded
    from any indicator the decision depends on. It exists to shift the
    decision bar onto D-1 and to represent the bar the fill belongs to.
    """
    import pandas as _pd
    if df is None or not len(df) or price is None:
        return df
    try:
        ts = _pd.Timestamp(session if session is not None
                            else _dt.date.today()).normalize()
        # build_dataframe hands back a tz-AWARE UTC index. A naive
        # timestamp raises "cannot compare tz-naive and tz-aware" inside
        # sort_index, which the guard below would swallow — the append
        # would silently no-op and the decision bar would stay a session
        # too old, which is the exact bug this function removes.
        frame_tz = getattr(df.index, "tz", None)
        if frame_tz is not None and ts.tz is None:
            ts = ts.tz_localize(frame_tz)
        elif frame_tz is None and ts.tz is not None:
            ts = ts.tz_localize(None)
        if ts in df.index:
            return df                      # vendor already published it
        p = float(price)
        row = {c: p for c in ("open", "high", "low", "close")}
        for col in df.columns:
            row.setdefault(col, df[col].iloc[-1])
        out = _pd.concat([df, _pd.DataFrame([row], index=[ts])])
        return out.sort_index()
    except Exception:  # noqa: BLE001
        return df


def in_closing_window(now=None, minutes: int = None) -> bool:
    """True inside the last `minutes` of a live session.

    Derived from the calendar's own close, never a constant: a window
    hardcoded to 15:45 would open three hours after a 13:00 half-day
    close and the sleeve would simply never trade that day.
    """
    span = int(minutes or STOCK_CLOSING_WINDOW_MINUTES)
    try:
        ref = now or datetime.now(tz=mc.ET)
        if not mc.is_market_open(ref):
            return False
        _open_ts, close_ts = mc.session_bounds(ref.astimezone(mc.ET).date())
        return (close_ts - ref.astimezone(mc.ET)).total_seconds() <= span * 60
    except Exception:  # noqa: BLE001
        # Fail shut: a missed day costs one skipped signal, while a
        # mistimed entry books the drift this whole change removes.
        return False


# ─── Bar cadence (Aug 14 2026) ───────────────────────────────────────────
#
# Every sleeve reads iloc[-2], the last COMPLETED daily bar, which does
# not change for a whole session. The daemon polls far more often than
# that, so without a gate it re-decides an identical bar every cycle.
# On the day the data layer was fixed, QQQ_REV opened and closed fifteen
# times in ninety minutes: enter (oversold on bar D) -> exit (close(D)
# above its 5-day mean) -> enter again. Both conditions can hold on the
# same bar, and a paper venue that models no fees reported the loop as
# a 4.38 profit factor.
#
# A daily sleeve acts at most once per completed daily bar.

def completed_bar_id(df) -> str | None:
    """Stable identity of the bar the sleeves actually read."""
    try:
        if df is None or len(df) < 2:
            return None
        ts = df.index[-2]
        return str(ts.date() if hasattr(ts, "date") else ts)
    except Exception:  # noqa: BLE001
        return None


def should_act_on_bar(state: dict, asset_name: str, bar_id) -> bool:
    """True only when this asset has not been acted on for this bar.

    An unknown bar id refuses: we cannot prove the bar is new, and a
    daily sleeve skipping one cycle costs nothing while churning costs
    real money.
    """
    if not bar_id:
        return False
    return (state.get("stock_last_bar") or {}).get(asset_name) != bar_id


def mark_bar_acted(state: dict, asset_name: str, bar_id) -> None:
    if bar_id:
        state.setdefault("stock_last_bar", {})[asset_name] = bar_id


def bars_held_since(df, entry_bar) -> int:
    """Completed bars between entry and now.

    Replaces pos["bars_held"], which was read by check_reversion_exit
    but written by nothing, so max_hold_bars was dead code. Unknown
    entry bar returns 0 — that delays a time stop rather than firing
    one at random on a legacy position.
    """
    if not entry_bar or df is None or len(df) < 2:
        return 0
    try:
        ids = [str(t.date() if hasattr(t, "date") else t) for t in df.index]
        return max(0, (len(ids) - 1) - 1 - ids.index(entry_bar))
    except (ValueError, AttributeError):
        return 0


def can_exit_on_bar(pos: dict, bar_id) -> bool:
    """A position never exits on the bar it entered on.

    Positions opened before this change carry no entry_bar and stay
    exitable — trapping one would be worse than the churn it prevents.
    """
    entry_bar = (pos or {}).get("entry_bar")
    if not entry_bar or not bar_id:
        return True
    return entry_bar != bar_id


def _exit_reason_for(executor, pos: dict) -> str | None:
    """Delegate to the sleeve's own live exit rule."""
    sleeve = pos.get("sleeve", "rev")
    symbol = pos.get("symbol")
    df = _frame(executor, symbol, "1d")
    if df is None or len(df) < 10:
        return None
    if not can_exit_on_bar(pos, completed_bar_id(df)):
        return None
    if sleeve == "trend":
        cfg = next((c for c in STOCK_TREND_ASSETS.values()
                     if c["symbol"] == symbol), {"sma_period": 200})
        return ss.check_trend_exit(df, cfg)
    if sleeve == "rev":
        cfg = next((c for c in STOCK_REV_ASSETS.values()
                     if c["symbol"] == symbol), {})
        return ss.check_reversion_exit(
            df, cfg, bars_held=bars_held_since(df, pos.get("entry_bar")),
            entry_price=float(pos.get("entry_price") or 0))
    return None          # dual exits by rotation, handled in the entry pass


def run_cycle(executor, state: dict) -> None:
    _write_heartbeat()

    # 1. Exits run even when paused or shut — an open position must
    #    always be manageable. (Orders placed outside hours simply queue.)
    for key, pos in list((state.get("positions") or {}).items()):
        if not key.startswith(STOCK_STATE_KEY_PREFIX):
            continue
        try:
            reason = _exit_reason_for(executor, pos)
        except Exception as e:  # noqa: BLE001
            logger.error("[%s] exit check failed: %s", key, e)
            continue
        if reason:
            close_stock_position(executor, state, key, reason)

    if not mc.is_market_open():
        save_state(state, owner="stock")
        return
    if STOCK_PAUSED:
        logger.info("STOCK_PAUSED=true — no new entries")
        save_state(state, owner="stock")
        return
    if _count_open(state) >= MAX_STOCK_POSITIONS:
        save_state(state, owner="stock")
        return

    status = state.setdefault("stock_signal_status", {})
    now_iso = datetime.now(timezone.utc).isoformat()
    rebalance = _is_rebalance_day()

    # Entries only near the bell. The replay fills at the session close;
    # entering at whatever moment a poll lands books an intraday drift
    # the backtest never paid. Exits above are deliberately outside this
    # gate — an open position must stay manageable all session.
    if not in_closing_window():
        save_state(state, owner="stock")
        return

    # 2. Reversion — daily cadence
    if not _sleeve_blocked("rev"):
        for name, cfg in STOCK_REV_ASSETS.items():
            key = _state_key(name, "rev")
            if key in (state.get("positions") or {}):
                continue
            df = _frame(executor, cfg["symbol"], "1d")
            if df is None:
                continue
            sig = ss.analyze_reversion_entry(df, cfg)
            bar_id = completed_bar_id(df)
            status[name] = {"sleeve": "rev", "symbol": cfg["symbol"],
                             "checked_at": now_iso, "bar": bar_id, **sig}
            if not should_act_on_bar(state, name, bar_id):
                continue
            if sig["would_enter"]:
                mark_bar_acted(state, name, bar_id)
                open_stock_position(executor, state, name, cfg, "rev",
                                      float(df["close"].iloc[-2]), bar_id)

    # 3. Trend — month-end only
    if rebalance and not _sleeve_blocked("trend"):
        for name, cfg in STOCK_TREND_ASSETS.items():
            key = _state_key(name, "trend")
            if key in (state.get("positions") or {}):
                continue
            df = _frame(executor, cfg["symbol"], "1d")
            if df is None:
                continue
            sig = ss.analyze_trend_entry(df, cfg)
            status[name] = {"sleeve": "trend", "symbol": cfg["symbol"],
                             "checked_at": now_iso, **sig}
            if sig["would_enter"]:
                open_stock_position(executor, state, name, cfg, "trend",
                                      float(df["close"].iloc[-2]))

    # 4. Dual momentum — month-end rotation
    if rebalance and not _sleeve_blocked("dual"):
        try:
            needed = (list(STOCK_DUAL_CONFIG["risk_assets"])
                       + [STOCK_DUAL_CONFIG["safe_asset"],
                          STOCK_DUAL_CONFIG["cash_asset"]])
            frames = {s: _frame(executor, s, "1d") for s in needed}
            if all(f is not None for f in frames.values()):
                vote = ss.dual_momentum_vote(frames, STOCK_DUAL_CONFIG)
                status["GEM"] = {"sleeve": "dual", "checked_at": now_iso,
                                  "would_enter": bool(vote.get("winner")),
                                  "blocked_by": vote.get("blocked_by"),
                                  "winner": vote.get("winner"),
                                  "agreement": vote.get("ensemble_agreement")}
                winner = vote.get("winner")
                if winner:
                    held = [k for k in (state.get("positions") or {})
                             if k.startswith(f"{STOCK_STATE_KEY_PREFIX}DUAL_")]
                    holding = (state["positions"][held[0]].get("symbol")
                                if held else None)
                    if holding != winner:
                        for k in held:
                            close_stock_position(executor, state, k,
                                                   f"Rotate -> {winner}")
                        cfg = {"symbol": winner,
                                "strategy_name":
                                    f"GEM 1M {STOCK_DUAL_CONFIG['strategy_name'].split()[-1]}"}
                        px = executor.get_symbol_price(winner)
                        if px:
                            open_stock_position(executor, state, winner,
                                                  cfg, "dual", float(px))
        except Exception as e:  # noqa: BLE001
            logger.error("dual sleeve failed: %s", e)

    save_state(state, owner="stock")


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    from stock_executor import StockExecutor
    from config import DRY_RUN

    logger.info("Stock daemon starting. PAUSED=%s DRY_RUN=%s calendar=%s",
                 STOCK_PAUSED, DRY_RUN, mc.backend_note())
    executor = StockExecutor(dry_run=DRY_RUN, paper=True)
    state = load_state()
    while True:
        started = time.time()
        try:
            run_cycle(executor, state)
        except Exception as e:  # noqa: BLE001
            logger.error("cycle errored: %s", e, exc_info=True)
        time.sleep(max(1.0, sleep_seconds() - (time.time() - started)))


if __name__ == "__main__":
    run()
