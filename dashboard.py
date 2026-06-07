"""Dashboard generator — produces a dark-themed HTML dashboard.

Pulls data from WEEX API and the Trading Journal, then writes
a self-contained dashboard.html with Chart.js charts.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

BOT_DIR = Path(__file__).resolve().parent

try:
    from zoneinfo import ZoneInfo
    CENTRAL_TZ = ZoneInfo("America/Chicago")
except ImportError:
    # Fallback for Python <3.9 (should not be needed but safe)
    import pytz
    CENTRAL_TZ = pytz.timezone("America/Chicago")

from config import (
    JOURNAL_FILE, DASHBOARD_FILE, INITIAL_CAPITAL,
    ASSETS, MARGIN_PER_TRADE, DEFAULT_LEVERAGE, MAX_POSITIONS,
    BACKTEST_YEARS, BACKTEST_CAPITAL, BACKTEST_QTY_PCT,
    DRY_RUN,
)
from blocker_labels import blocker_label

# Whale bot data (optional — dashboard still renders if whale_config missing)
try:
    from whale_config import (
        WHALE_SIGNAL_LOG, WHALE_STATE_KEY_PREFIX, WHALE_STRATEGY_TAG,
    )
    WHALE_AVAILABLE = True
except ImportError:
    WHALE_SIGNAL_LOG = None
    WHALE_STATE_KEY_PREFIX = "WHALE_"
    WHALE_STRATEGY_TAG = "Whale Track"
    WHALE_AVAILABLE = False

# Funding bot data (optional — dashboard renders without it)
try:
    from funding_config import (
        FUNDING_SIGNAL_LOG, FUNDING_STATE_KEY_PREFIX, FUNDING_STRATEGY_TAG,
    )
    FUNDING_AVAILABLE = True
except ImportError:
    FUNDING_SIGNAL_LOG = None
    FUNDING_STATE_KEY_PREFIX = "FUNDING_"
    FUNDING_STRATEGY_TAG = "Funding Fade"
    FUNDING_AVAILABLE = False

logger = logging.getLogger("crypto_bot.dashboard")


def _read_journal_trades(max_rows: int = 5000) -> List[dict]:
    """Read trade records from trades.jsonl. PnL fields are computed on
    the fly by journal.read_trades() so this dashboard doesn't need to
    know the schema."""
    try:
        from journal import read_trades  # local import to avoid circulars at module load
        return read_trades(max_rows=max_rows)
    except Exception as e:
        logger.error("Failed to read journal: %s", e)
        return []


def _compute_metrics(trades: List[dict]) -> dict:
    """Compute performance metrics from trade history.

    Phase H added Sortino, Calmar (90d), Ulcer Index, time-to-recovery,
    per-regime expectancy, and replaced the hardcoded 72-trades/year
    Sharpe constant with an observed-frequency annualization.
    """
    import statistics
    import metrics as _m

    if not trades:
        return {
            "win_rate": 0, "profit_factor": 0, "avg_win": 0, "avg_loss": 0,
            "best_trade": 0, "worst_trade": 0, "max_drawdown": 0,
            "sharpe": 0, "sortino": 0, "calmar": 0, "ulcer_index": 0,
            "time_to_recovery_bars": 0,
            "expectancy": 0, "total_trades": 0,
            "regime_expectancy": {},
        }

    # Win rate denominator must be CLOSED trades only.
    closed = [t for t in trades if t.get("result") in ("WIN", "LOSS", "FLAT")
              and t.get("exit_price") not in (None, 0, "0", "")]
    open_count = len(trades) - len(closed)

    wins = [t for t in closed if (t.get("net_pnl") or 0) > 0]
    losses = [t for t in closed if (t.get("net_pnl") or 0) < 0]
    pnls = [float(t.get("net_pnl") or 0) for t in closed]

    total_closed = len(closed)
    win_count = len(wins)
    win_rate = (win_count / total_closed * 100) if total_closed > 0 else 0

    gross_profit = sum(float(t.get("net_pnl") or 0) for t in wins)
    gross_loss = abs(sum(float(t.get("net_pnl") or 0) for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 999

    avg_win = (gross_profit / win_count) if win_count > 0 else 0
    avg_loss = (gross_loss / len(losses)) if losses else 0

    best = max(pnls) if pnls else 0
    worst = min(pnls) if pnls else 0

    # Equity curve from initial capital (closed trades only)
    equity_curve = [INITIAL_CAPITAL]
    running = INITIAL_CAPITAL
    for pnl in pnls:
        running += pnl
        equity_curve.append(running)

    # Observed window for annualization. Falls back to a reasonable default
    # if no date parsing is possible.
    days_observed = _observed_window_days(closed) or 30

    sharpe   = _m.annualized_sharpe(pnls, days_observed=days_observed)
    sortino  = _m.sortino(pnls, trades_per_year=max(1,
                  int(len(pnls) * 365 / max(days_observed, 1))))
    calmar90 = _m.calmar(pnls[-_recent_trades_in_window(closed, 90):]
                         or pnls, initial_equity=INITIAL_CAPITAL, days=90)
    ulcer    = _m.ulcer_index(equity_curve)
    ttr_bars = _m.time_to_recovery(equity_curve)
    regime_exp = _m.per_regime_expectancy(closed)
    max_dd   = _m.max_drawdown(equity_curve)
    # J.7: new KPIs
    streak_count, streak_type = _m.consecutive_streak(pnls)
    recovery   = _m.recovery_factor(pnls, initial_equity=INITIAL_CAPITAL)

    expectancy = statistics.mean(pnls) if pnls else 0

    return {
        "win_rate":              round(win_rate, 1),
        "profit_factor":         round(profit_factor, 2),
        "avg_win":               round(avg_win, 2),
        "avg_loss":              round(avg_loss, 2),
        "best_trade":            round(best, 2),
        "worst_trade":           round(worst, 2),
        "max_drawdown":          round(max_dd, 1),
        "sharpe":                sharpe,
        "sortino":               round(sortino, 2),
        "calmar":                calmar90,
        "ulcer_index":           ulcer,
        "time_to_recovery_bars": ttr_bars,
        "expectancy":            round(expectancy, 2),
        "total_trades":          total_closed,
        "open_positions":        open_count,
        "all_trades_count":      len(trades),
        "regime_expectancy":     regime_exp,
        "days_observed":         days_observed,
        "streak_count":          streak_count,
        "streak_type":           streak_type,
        "recovery_factor":       recovery,
    }


def _observed_window_days(closed_trades: List[dict]) -> int:
    """Span in days between the earliest and latest closed-trade timestamp.

    Returns 0 if dates can't be parsed. Powers the annualization factor
    for Sharpe/Sortino instead of the hardcoded 72-trades/year constant.
    """
    if not closed_trades:
        return 0
    dts = []
    for t in closed_trades:
        s = (t.get("date_closed") or t.get("date_opened") or "").strip()
        if not s:
            continue
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            dts.append(dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))
        except ValueError:
            continue
    if len(dts) < 2:
        return 0
    span = (max(dts) - min(dts)).days
    return max(1, span)


def _recent_trades_in_window(closed_trades: List[dict], days: int) -> int:
    """Number of closed trades whose date_closed is within the last N days."""
    if not closed_trades:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    count = 0
    for t in closed_trades:
        s = (t.get("date_closed") or t.get("date_opened") or "").strip()
        if not s:
            continue
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if dt >= cutoff:
            count += 1
    return count


def _compute_bot_status() -> Dict[str, dict]:
    """Read heartbeat mtimes for both bots and classify as LIVE / STALE / NEVER.

    Momentum bot saves state.json every cycle (~5 min). Whale bot writes
    .whale_heartbeat every cycle (~15 min). Stale threshold = 2x poll interval.
    """
    from datetime import datetime, timedelta, timezone
    import time

    bot_dir = Path(__file__).resolve().parent
    now = time.time()

    def _classify(path: Path, fresh_threshold_s: int) -> dict:
        if not path.exists():
            return {"text": "NEVER", "css": "never", "age": "never run"}
        age_s = now - path.stat().st_mtime
        if age_s < fresh_threshold_s:
            css, text = "live", "LIVE"
        elif age_s < fresh_threshold_s * 4:
            css, text = "stale", "STALE"
        else:
            css, text = "stale", "DOWN"
        if age_s < 60:
            age = f"{int(age_s)}s ago"
        elif age_s < 3600:
            age = f"{int(age_s / 60)}m ago"
        elif age_s < 86400:
            age = f"{int(age_s / 3600)}h ago"
        else:
            age = f"{int(age_s / 86400)}d ago"
        return {"text": text, "css": css, "age": age}

    # Momentum: state.json — fresh if updated in last 10 min (2x its 5min poll)
    momentum = _classify(bot_dir / "state.json", 600)
    # Whale: .whale_heartbeat — fresh if updated in last 30 min (2x its 15min poll)
    whale = _classify(bot_dir / ".whale_heartbeat", 1800)
    # Funding: .funding_heartbeat — fresh if updated in last 2 hours (2x its 1h poll)
    funding = _classify(bot_dir / ".funding_heartbeat", 7200)
    # Breakout: .breakout_heartbeat — 5-min poll, fresh threshold 10min
    breakout = _classify(bot_dir / ".breakout_heartbeat", 600)
    # Pair: .pair_heartbeat — 5-min poll, fresh threshold 10min
    pair = _classify(bot_dir / ".pair_heartbeat", 600)
    # Reversal: .reversal_heartbeat — 5-min poll, fresh threshold 10min
    reversal = _classify(bot_dir / ".reversal_heartbeat", 600)

    return {
        "momentum": momentum,
        "whale":    whale,
        "funding":  funding,
        "breakout": breakout,
        "pair":     pair,
        "reversal": reversal,
    }


def _state_positions_as_exchange_shape(state: dict, executor) -> List[dict]:
    """Convert state.json paper positions into the WEEX exchange-position shape.

    Used in DRY_RUN where no real orders are placed but the bots still track
    paper trades in state.json. Each returned dict carries an `is_paper` flag
    so the renderer can tag it visually.
    """
    positions = state.get("positions", {})
    if not positions:
        return []

    out = []
    for state_key, pos in positions.items():
        symbol = pos.get("symbol", state_key)
        try:
            qty = float(pos.get("quantity", 0))
        except (TypeError, ValueError):
            qty = 0.0
        if qty <= 0:
            continue

        direction = pos.get("direction", "LONG")  # legacy momentum positions default to LONG
        signed_qty = qty if direction == "LONG" else -qty

        try:
            entry_price = float(pos.get("entry_price", 0) or 0)
        except (TypeError, ValueError):
            entry_price = 0.0

        # Best-effort current price for uPnL display
        mark_price = entry_price
        try:
            current = executor.get_symbol_price(symbol)
            if current and current > 0:
                mark_price = float(current)
        except Exception:
            pass

        # Direction-aware uPnL
        sign = 1 if direction == "LONG" else -1
        upnl = (mark_price - entry_price) * qty * sign

        leverage = pos.get("leverage", DEFAULT_LEVERAGE) or DEFAULT_LEVERAGE
        margin_usd = pos.get("margin_usd")
        if margin_usd is None and entry_price > 0 and qty > 0 and leverage:
            margin_usd = (entry_price * qty) / leverage

        out.append({
            "symbol": symbol,
            "positionAmt": str(signed_qty),
            "entryPrice": str(entry_price),
            "markPrice": str(mark_price),
            "leverage": str(leverage),
            "unrealizedProfit": str(round(upnl, 4)),
            "positionInitialMargin": str(round(margin_usd or 0, 2)),
            "liquidationPrice": pos.get("liquidation_price") or "N/A",
            "is_paper": True,
            "state_key": state_key,
            "strategy": pos.get("strategy", ""),
        })
    return out


def gather_dashboard_data(executor, state: dict) -> Dict[str, Any]:
    """Gather all data needed for the dashboard."""
    data: Dict[str, Any] = {}

    # Account balance
    bal = executor.get_account_balance()
    data["balance"] = bal
    data["equity"] = float(bal.get("balance", 0) or 0)
    data["available"] = float(bal.get("availableBalance", 0) or 0)
    data["unrealized_pnl"] = float(bal.get("unrealizePnl", 0) or 0)

    # Open positions — prefer real exchange data, fall back to paper trades
    # from state.json when in DRY_RUN (where the exchange has nothing to return).
    positions = executor.get_all_positions()
    if not positions and DRY_RUN:
        positions = _state_positions_as_exchange_shape(state, executor)
    data["positions"] = positions

    # Funding rates (top 10)
    rates = executor.get_funding_rate()
    for r in rates:
        r["abs_rate"] = abs(float(r.get("lastFundingRate", "0")))
        rate_val = float(r.get("lastFundingRate", "0"))
        r["annualized"] = round(rate_val * 3 * 365 * 100, 1)
        r["direction"] = "SHORT" if rate_val > 0 else "LONG"
    rates.sort(key=lambda x: x["abs_rate"], reverse=True)
    data["funding_rates"] = rates[:10]

    # Journal trades
    trades = _read_journal_trades()
    data["trades"] = trades
    data["recent_trades"] = trades[-20:] if trades else []

    # Performance metrics
    data["metrics"] = _compute_metrics(trades)

    # Daily PnL (last 30 days). Only count CLOSED trades — open positions
    # have date_closed=None in the JSONL and would otherwise bucket under
    # a single 'None' label, producing one giant column with no x-axis date.
    daily_pnl: Dict[str, float] = {}
    for t in trades:
        date_closed = t.get("date_closed")
        if not date_closed:
            continue
        date_str = str(date_closed)[:10]
        if not date_str or date_str == "None":
            continue
        daily_pnl[date_str] = daily_pnl.get(date_str, 0) + float(t.get("net_pnl") or 0)
    sorted_days = sorted(daily_pnl.items())[-30:]
    data["daily_pnl_labels"] = [d[0] for d in sorted_days]
    data["daily_pnl_values"] = [round(d[1], 2) for d in sorted_days]

    # Equity curve — also only step on closed trades. Open positions
    # contribute 0 net_pnl per the journal enricher, so including them
    # would just flatline the curve for each open trade.
    equity_curve = [INITIAL_CAPITAL]
    for t in trades:
        if not t.get("date_closed"):
            continue
        equity_curve.append(round(equity_curve[-1] + float(t.get("net_pnl") or 0), 2))
    data["equity_curve"] = equity_curve

    # Portfolio allocation
    allocation: Dict[str, float] = {}
    for p in positions:
        sym = p.get("symbol", "?")
        margin = float(p.get("positionInitialMargin", 0) or 0)
        allocation[sym] = allocation.get(sym, 0) + margin
    data["allocation"] = allocation

    # v2: Signal status per asset (for Entry Signal Diagnostics panel)
    data["signal_status"] = state.get("signal_status", {})

    # Bot health status (read mtimes; classify as LIVE/STALE/NEVER)
    data["bot_status"] = _compute_bot_status()

    # Whale bot data (open whale positions + most recent signal snapshot)
    whale_positions = []
    for key, pos in state.get("positions", {}).items():
        if not key.startswith(WHALE_STATE_KEY_PREFIX):
            continue
        whale_positions.append({
            "state_key": key,
            "coin": key.replace(WHALE_STATE_KEY_PREFIX, ""),
            "symbol": pos.get("symbol", ""),
            "direction": pos.get("direction", "LONG"),
            "entry_price": pos.get("entry_price", 0.0),
            "quantity": pos.get("quantity", 0),
            "sl": pos.get("sl"),
            "tp": pos.get("tp"),
            "signal_type": pos.get("signal_type", ""),
            "confidence": pos.get("confidence", 0),
            "entry_time": pos.get("entry_time", ""),
            "strategy": pos.get("strategy", ""),
            "margin_usd": pos.get("margin_usd", 0),
        })
    data["whale_positions"] = whale_positions

    # Most recent whale signal scan (latest timestamp in JSONL)
    whale_signals_latest = []
    whale_signals_ts = None
    if WHALE_SIGNAL_LOG and WHALE_SIGNAL_LOG.exists():
        try:
            with open(WHALE_SIGNAL_LOG, "r", encoding="utf-8") as f:
                lines = f.readlines()[-200:]  # last 200 records
            recs = [json.loads(l) for l in lines if l.strip()]
            if recs:
                whale_signals_ts = recs[-1].get("timestamp")
                whale_signals_latest = [r for r in recs if r.get("timestamp") == whale_signals_ts]
        except Exception as e:
            logger.warning("Could not read whale signal log: %s", e)
    data["whale_signals_latest"] = whale_signals_latest
    data["whale_signals_ts"] = whale_signals_ts or "never"

    # Whale-specific trade stats (filtered journal)
    whale_trades = [t for t in trades if isinstance(t.get("strategy"), str)
                    and t["strategy"].startswith(WHALE_STRATEGY_TAG)]
    data["whale_trades"] = whale_trades
    data["whale_metrics"] = _compute_metrics(whale_trades)

    # ─── Funding-fade bot data ───────────────────────────────────────────
    funding_positions = []
    for key, pos in state.get("positions", {}).items():
        if not key.startswith(FUNDING_STATE_KEY_PREFIX):
            continue
        funding_positions.append({
            "state_key": key,
            "coin": key.replace(FUNDING_STATE_KEY_PREFIX, ""),
            "symbol": pos.get("symbol", ""),
            "direction": pos.get("direction", "LONG"),
            "entry_price": pos.get("entry_price", 0.0),
            "quantity": pos.get("quantity", 0),
            "sl": pos.get("sl"),
            "tp": pos.get("tp"),
            "signal_type": pos.get("signal_type", ""),
            "confidence": pos.get("confidence", 0),
            "entry_time": pos.get("entry_time_iso") or pos.get("entry_time", ""),
            "strategy": pos.get("strategy", ""),
            "margin_usd": pos.get("margin_usd", 0),
            "funding_at_entry": pos.get("funding_at_entry", 0),
        })
    data["funding_positions"] = funding_positions

    funding_signals_latest = []
    funding_signals_ts = None
    if FUNDING_SIGNAL_LOG and FUNDING_SIGNAL_LOG.exists():
        try:
            with open(FUNDING_SIGNAL_LOG, "r", encoding="utf-8") as f:
                lines = f.readlines()[-200:]
            recs = [json.loads(l) for l in lines if l.strip()]
            if recs:
                funding_signals_ts = recs[-1].get("timestamp")
                funding_signals_latest = [r for r in recs
                                          if r.get("timestamp") == funding_signals_ts]
        except Exception as e:
            logger.warning("Could not read funding signal log: %s", e)
    data["funding_signals_latest"] = funding_signals_latest
    data["funding_signals_ts"] = funding_signals_ts or "never"

    funding_trades = [t for t in trades if isinstance(t.get("strategy"), str)
                      and t["strategy"].startswith(FUNDING_STRATEGY_TAG)]
    data["funding_trades"] = funding_trades
    data["funding_metrics"] = _compute_metrics(funding_trades)

    # Show timestamp in Central Time (auto-handles CST/CDT)
    data["timestamp"] = datetime.now(CENTRAL_TZ).strftime("%Y-%m-%d %H:%M %Z")
    return data


def _compute_yearly_projection() -> dict:
    """Build per-strategy yearly profit projection.

    Scales backtested $ P&L to user's live sizing:
        scale = (MARGIN_PER_TRADE * DEFAULT_LEVERAGE) / (BACKTEST_CAPITAL * BACKTEST_QTY_PCT/100)
    Annualizes by dividing the backtest total by BACKTEST_YEARS.
    """
    live_notional = MARGIN_PER_TRADE * DEFAULT_LEVERAGE
    backtest_notional = BACKTEST_CAPITAL * (BACKTEST_QTY_PCT / 100)
    scale = live_notional / backtest_notional

    rows = []
    total_annual = 0.0
    total_trades_per_year = 0.0
    for key, cfg in ASSETS.items():
        stats = cfg.get("backtest_stats")
        if not stats:
            continue
        pf = stats.get("pf", 0)
        trades = stats.get("trades", 0)
        pnl_pct = stats.get("pnl_pct", 0)
        dd = stats.get("dd_pct", 0)

        total_pnl_backtest = (pnl_pct / 100.0) * BACKTEST_CAPITAL
        annual_pnl_backtest = total_pnl_backtest / BACKTEST_YEARS
        annual_pnl_live = annual_pnl_backtest * scale
        trades_per_year = trades / BACKTEST_YEARS

        rows.append({
            "key": key,
            "name": cfg.get("strategy_name", key),
            "symbol": cfg.get("symbol", ""),
            "interval": cfg.get("interval", ""),
            "pf": pf,
            "trades_per_year": trades_per_year,
            "annual_pct_backtest": pnl_pct / BACKTEST_YEARS,
            "annual_pnl_live": annual_pnl_live,
            "dd_pct": dd,
        })
        total_annual += annual_pnl_live
        total_trades_per_year += trades_per_year

    # Whale bot — uses its own synthetic-proxy backtest (24mo window) and
    # always trades at $500 notional (matches live config), so scale = 1.
    try:
        from whale_config import WHALE_BACKTEST_STATS, WHALE_MARGIN_CONSENSUS, WHALE_LEVERAGE
        whale_stats = WHALE_BACKTEST_STATS
        whale_years = whale_stats.get("years", 2.0)
        whale_total_pnl_backtest = (whale_stats["pnl_pct"] / 100.0) * BACKTEST_CAPITAL
        whale_annual_pnl_backtest = whale_total_pnl_backtest / whale_years
        # Whale lives at WHALE_MARGIN_CONSENSUS x WHALE_LEVERAGE notional
        whale_live_notional = WHALE_MARGIN_CONSENSUS * WHALE_LEVERAGE
        whale_scale = whale_live_notional / backtest_notional if backtest_notional > 0 else 1.0
        whale_annual_pnl_live = whale_annual_pnl_backtest * whale_scale
        whale_trades_per_year = whale_stats["trades"] / whale_years

        rows.append({
            "key": "WHALE",
            "name": whale_stats.get("name", "Whale Tracker"),
            "symbol": "Multi-asset",
            "interval": "15m poll",
            "pf": whale_stats["pf"],
            "trades_per_year": whale_trades_per_year,
            "annual_pct_backtest": whale_stats["pnl_pct"] / whale_years,
            "annual_pnl_live": whale_annual_pnl_live,
            "dd_pct": whale_stats["dd_pct"],
            "is_whale": True,
            "source_note": whale_stats.get("source", ""),
        })
        total_annual += whale_annual_pnl_live
        total_trades_per_year += whale_trades_per_year
    except ImportError:
        # whale_config not available — render without the whale row
        pass

    rows.sort(key=lambda r: r["annual_pnl_live"], reverse=True)
    return {
        "rows": rows,
        "total_annual": total_annual,
        "total_trades_per_year": total_trades_per_year,
        "live_notional": live_notional,
        "scale": scale,
    }


def _resolve_build_sha() -> str:
    """Resolve the BUILD chip's commit hash for the colophon.

    Precedence:
      1. GIT_SHA env var (deployer override)
      2. `git rev-parse --short=8 HEAD` against BOT_DIR
      3. "local" fallback
    """
    env_sha = os.getenv("GIT_SHA", "").strip()
    if env_sha:
        return env_sha[:8]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=str(BOT_DIR),
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
            if sha:
                return sha[:8]
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass
    return "local"


def _build_v2_context(data: Dict[str, Any], state: dict | None = None,
                        executor=None) -> Dict[str, Any]:
    """Shape gather_dashboard_data output for the Jinja2 templates.

    Per-bot stats are recomputed by filtering trades on the `bot` column —
    cheap enough at this scale (a few hundred rows max).
    """
    trades = _read_journal_trades()

    def _bot_card(bot_class, monogram, name, bot_label, status):
        bot_trades = [t for t in trades if t.get("bot") == bot_label]
        closed = [t for t in bot_trades
                  if t.get("exit_price") not in (None, 0, "0", "")]
        net_pnl = sum(float(t.get("net_pnl") or 0) for t in closed)
        wins = sum(1 for t in closed if (t.get("net_pnl") or 0) > 0)
        win_rate = (wins / len(closed) * 100) if closed else 0.0
        pnl_trend = _v2_trend(trades, bot_label, "net_pnl",  days=30)
        wr_trend  = _v2_trend(trades, bot_label, "win_rate", days=30)
        return {
            "class":    bot_class,
            "monogram": monogram,
            "name":     name,
            "state":    _v2_state(bot_class, status),
            "seen_label": _v2_seen_label(bot_class, status),
            "net_pnl":  net_pnl,
            "net_pnl_display": _v2_pnl_display(net_pnl),
            "trade_count": len(closed),
            "win_rate_display": (f"{win_rate:.0f}%" if closed else "—"),
            "pnl_trend":  {**pnl_trend, "glyph": _v2_trend_glyph(pnl_trend["direction"])},
            "wr_trend":   {**wr_trend,  "glyph": _v2_trend_glyph(wr_trend["direction"])},
        }

    bot_status = data.get("bot_status") or {}
    metrics = data.get("metrics") or {}

    portfolio_closed = [t for t in trades
                        if t.get("exit_price") not in (None, 0, "0", "")]
    portfolio_net = sum(float(t.get("net_pnl") or 0) for t in portfolio_closed)

    # Sparklines — last 30d cumulative PnL per bot + portfolio aggregate.
    spark_momentum = _v2_sparkline_points(trades, "Momentum", days=30)
    spark_whale    = _v2_sparkline_points(trades, "Whale",    days=30)
    spark_funding  = _v2_sparkline_points(trades, "Funding",  days=30)
    spark_breakout = _v2_sparkline_points(trades, "Breakout", days=30)
    spark_pair     = _v2_sparkline_points(trades, "Pair",     days=30)
    spark_reversal = _v2_sparkline_points(trades, "Reversal", days=30)
    spark_portfolio = _v2_sparkline_points(trades, None,      days=30)

    # Stash trades on the data dict so _v2_why_silent() can read them
    # without us threading them through every call site.
    data["_trades_cache"] = trades

    now = datetime.now(timezone.utc)
    return {
        "operator":  os.getenv("OPERATOR", "ayott84"),
        "env":       "paper" if DRY_RUN else "live",
        "freshness": "0s",
        "build_sha": _resolve_build_sha(),
        "build_ts":  now.strftime("%Y-%m-%d %H:%M UTC"),
        "bots": [
            {**_bot_card("momentum", "M", "Momentum", "Momentum",
                         bot_status.get("momentum", {})),
             "spark_svg": _v2_sparkline_svg(
                 spark_momentum,
                 stroke_class="spark__line spark__line--momentum",
                 label="Momentum 30-day cumulative PnL"),
             "why":       _v2_why_silent("momentum", data)},
            {**_bot_card("whale",    "W", "Whale",    "Whale",
                         bot_status.get("whale", {})),
             "spark_svg": _v2_sparkline_svg(
                 spark_whale,
                 stroke_class="spark__line spark__line--whale",
                 label="Whale 30-day cumulative PnL"),
             "why":       _v2_why_silent("whale", data)},
            {**_bot_card("funding",  "F", "Funding",  "Funding",
                         bot_status.get("funding", {})),
             "spark_svg": _v2_sparkline_svg(
                 spark_funding,
                 stroke_class="spark__line spark__line--funding",
                 label="Funding 30-day cumulative PnL"),
             "why":       _v2_why_silent("funding", data)},
            {**_bot_card("breakout", "B", "Breakout", "Breakout",
                         bot_status.get("breakout", {})),
             "spark_svg": _v2_sparkline_svg(
                 spark_breakout,
                 stroke_class="spark__line spark__line--breakout",
                 label="Breakout 30-day cumulative PnL"),
             "why":       _v2_why_silent("breakout", data)},
            {**_bot_card("pair",     "P", "Pair",     "Pair",
                         bot_status.get("pair", {})),
             "spark_svg": _v2_sparkline_svg(
                 spark_pair,
                 stroke_class="spark__line spark__line--pair",
                 label="Pair 30-day cumulative PnL"),
             "why":       _v2_why_silent("pair", data)},
            {**_bot_card("reversal", "R", "Reversal", "Reversal",
                         bot_status.get("reversal", {})),
             "spark_svg": _v2_sparkline_svg(
                 spark_reversal,
                 stroke_class="spark__line spark__line--reversal",
                 label="Reversal 30-day cumulative PnL"),
             "why":       _v2_why_silent("reversal", data)},
        ],
        "portfolio": {
            "net_pnl":          portfolio_net,
            "net_pnl_display":  _v2_pnl_display(portfolio_net),
            "closed_count":     len(portfolio_closed),
            "open_count":       metrics.get("open_positions", 0),
            "win_rate_display": f"{metrics.get('win_rate', 0):.1f}%",
            "spark_svg":        _v2_sparkline_svg(
                spark_portfolio, width=200, height=32,
                stroke_class="spark__line",
                label="Portfolio 30-day cumulative PnL"),
        },
        "trades": _v2_trade_rows(trades),
        "momentum_meta": _v2_momentum_meta(trades),
        "whale_meta":    _v2_whale_meta(trades),
        "funding_meta":  _v2_funding_meta(trades),
        "breakout_meta": _v2_breakout_meta(trades),
        "pair_meta":     _v2_pair_meta(trades, state),
        # J.4: per-bot positions / trades / scoped KPIs
        "bot_panels": {
            "momentum": _v2_build_bot_panels(trades, state, "momentum"),
            "whale":    _v2_build_bot_panels(trades, state, "whale"),
            "funding":  _v2_build_bot_panels(trades, state, "funding"),
            "breakout": _v2_build_bot_panels(trades, state, "breakout"),
            "pair":     _v2_build_bot_panels(trades, state, "pair"),
            "reversal": _v2_build_bot_panels(trades, state, "reversal"),
        },
        "reversal_meta": _v2_reversal_meta(trades),
        # J.5a: per-bot chart panels (asset dropdown + chart data per asset)
        "chart_panels_root": _v2_build_all_chart_panels(executor, trades),
        "projection":   _v2_projection(),
        "equity_curve_svg": _v2_equity_curve_svg(
            _v2_equity_series(trades, days=90)),
        "daily_pnl_svg":    _v2_daily_pnl_svg(
            _v2_daily_pnl_bars(trades, days=30)),
        "risk_metrics":      _v2_risk_metrics(metrics),
        "regime_expectancy": _v2_regime_expectancy(metrics),
    }


def _v2_risk_metrics(metrics: dict) -> dict:
    """Shape the H-phase risk metrics for the Overview panel.

    Pre-formats each value as a display string so the template stays dumb.
    Cap values render specially (Sortino/Calmar with sentinel 999 = no DD).
    """
    def _ratio(v):
        if v is None or v == 0:
            return "—"
        if v >= 999 or v <= -999:
            return "∞"
        return f"{v:+.2f}"

    # J.7: streak + recovery factor display
    streak_count = int(metrics.get("streak_count", 0) or 0)
    streak_type  = metrics.get("streak_type", "") or ""
    if streak_count == 0:
        streak_display = "—"
        streak_class   = "is-flat"
    else:
        streak_display = f"{streak_count} {streak_type}"
        streak_class   = "is-up" if streak_type == "WIN" else "is-down"

    return {
        "sortino_display":     _ratio(metrics.get("sortino", 0)),
        "calmar_display":      _ratio(metrics.get("calmar", 0)),
        "ulcer_display":       (f"{metrics.get('ulcer_index', 0):.2f}"
                                if metrics.get("ulcer_index") else "—"),
        "max_dd_display":      (f"{metrics.get('max_drawdown', 0):.1f}%"
                                if metrics.get("max_drawdown") else "—"),
        "time_underwater":     metrics.get("time_to_recovery_bars", 0),
        "time_underwater_display": (
            "at peak" if metrics.get("time_to_recovery_bars", 0) == 0
            else f"{metrics.get('time_to_recovery_bars', 0)} bars"),
        "days_observed":       metrics.get("days_observed", 0),
        "streak_display":      streak_display,
        "streak_class":        streak_class,
        "recovery_display":    _ratio(metrics.get("recovery_factor", 0)),
    }


def _v2_regime_expectancy(metrics: dict) -> dict:
    """Sort the per-regime expectancy buckets for stable display order.

    Returns a list of rows with pre-formatted PnL + WR strings. Empty when
    no trades have a regime_at_entry tag (the case until B.3b backfill
    runs).
    """
    raw = metrics.get("regime_expectancy") or {}
    rows = []
    for regime, stats in sorted(raw.items()):
        rows.append({
            "regime":            regime,
            "count":             stats.get("count", 0),
            "expectancy_display": _v2_pnl_display(stats.get("expectancy", 0)),
            "win_rate_display":  f"{stats.get('win_rate', 0):.0f}%",
            "total_pnl_display": _v2_pnl_display(stats.get("total_pnl", 0)),
        })
    return {"rows": rows, "has_data": bool(rows)}


def _v2_test_context(trades: list | None = None, **overrides) -> dict:
    """Build a complete V2 template context using the real shapers.

    Centralized so test helpers don't drift behind context-key additions
    in _build_v2_context(). Pass `trades=[]` for the zero-state, or a
    list of trade dicts for non-empty cases. Override any top-level key
    via kwargs.
    """
    trades = trades or []
    # _v2_why_silent reads data["_trades_cache"]; emulate that shape for tests
    data_stub = overrides.pop("data", {"signal_status": {}, "_trades_cache": trades})

    def _trend_pair(bot_label):
        p = _v2_trend(trades, bot_label, "net_pnl", days=30)
        w = _v2_trend(trades, bot_label, "win_rate", days=30)
        return (
            {**p, "glyph": _v2_trend_glyph(p["direction"])},
            {**w, "glyph": _v2_trend_glyph(w["direction"])},
        )

    p_m, w_m = _trend_pair("Momentum")
    p_w, w_w = _trend_pair("Whale")
    p_f, w_f = _trend_pair("Funding")
    p_b, w_b = _trend_pair("Breakout")
    p_p, w_p = _trend_pair("Pair")
    p_r, w_r = _trend_pair("Reversal")
    ctx = {
        "operator": "ayott84", "env": "paper", "freshness": "0s",
        "build_sha": "abc12345", "build_ts": "2026-06-05 00:00 UTC",
        "bots": [
            {"class": "momentum", "monogram": "M", "name": "Momentum",
             "state": "live", "seen_label": "0s ago",
             "net_pnl": 0, "net_pnl_display": "$0.00",
             "trade_count": 0, "win_rate_display": "—",
             "pnl_trend": p_m, "wr_trend": w_m,
             "spark_svg": _v2_sparkline_svg(
                 _v2_sparkline_points(trades, "Momentum"),
                 stroke_class="spark__line spark__line--momentum",
                 label="Momentum 30-day cumulative PnL"),
             "why": _v2_why_silent("momentum", data_stub)},
            {"class": "whale", "monogram": "W", "name": "Whale",
             "state": "dormant", "seen_label": "paused",
             "net_pnl": 0, "net_pnl_display": "$0.00",
             "trade_count": 0, "win_rate_display": "—",
             "pnl_trend": p_w, "wr_trend": w_w,
             "spark_svg": _v2_sparkline_svg(
                 _v2_sparkline_points(trades, "Whale"),
                 stroke_class="spark__line spark__line--whale",
                 label="Whale 30-day cumulative PnL"),
             "why": _v2_why_silent("whale", data_stub)},
            {"class": "funding", "monogram": "F", "name": "Funding",
             "state": "live", "seen_label": "0s ago",
             "net_pnl": 0, "net_pnl_display": "$0.00",
             "trade_count": 0, "win_rate_display": "—",
             "pnl_trend": p_f, "wr_trend": w_f,
             "spark_svg": _v2_sparkline_svg(
                 _v2_sparkline_points(trades, "Funding"),
                 stroke_class="spark__line spark__line--funding",
                 label="Funding 30-day cumulative PnL"),
             "why": _v2_why_silent("funding", data_stub)},
            {"class": "breakout", "monogram": "B", "name": "Breakout",
             "state": "dormant", "seen_label": "paused",
             "net_pnl": 0, "net_pnl_display": "$0.00",
             "trade_count": 0, "win_rate_display": "—",
             "pnl_trend": p_b, "wr_trend": w_b,
             "spark_svg": _v2_sparkline_svg(
                 _v2_sparkline_points(trades, "Breakout"),
                 stroke_class="spark__line spark__line--breakout",
                 label="Breakout 30-day cumulative PnL"),
             "why": _v2_why_silent("breakout", data_stub)},
            {"class": "pair", "monogram": "P", "name": "Pair",
             "state": "dormant", "seen_label": "paused",
             "net_pnl": 0, "net_pnl_display": "$0.00",
             "trade_count": 0, "win_rate_display": "—",
             "pnl_trend": p_p, "wr_trend": w_p,
             "spark_svg": _v2_sparkline_svg(
                 _v2_sparkline_points(trades, "Pair"),
                 stroke_class="spark__line spark__line--pair",
                 label="Pair 30-day cumulative PnL"),
             "why": _v2_why_silent("pair", data_stub)},
            {"class": "reversal", "monogram": "R", "name": "Reversal",
             "state": "dormant", "seen_label": "paused",
             "net_pnl": 0, "net_pnl_display": "$0.00",
             "trade_count": 0, "win_rate_display": "—",
             "pnl_trend": p_r, "wr_trend": w_r,
             "spark_svg": _v2_sparkline_svg(
                 _v2_sparkline_points(trades, "Reversal"),
                 stroke_class="spark__line spark__line--reversal",
                 label="Reversal 30-day cumulative PnL"),
             "why": _v2_why_silent("reversal", data_stub)},
        ],
        "portfolio": {"net_pnl": 0, "net_pnl_display": "$0.00",
                      "closed_count": 0, "open_count": 0,
                      "win_rate_display": "—",
                      "spark_svg": _v2_sparkline_svg(
                          _v2_sparkline_points(trades), width=200, height=32,
                          stroke_class="spark__line",
                          label="Portfolio 30-day cumulative PnL")},
        "trades":        _v2_trade_rows(trades),
        "momentum_meta": _v2_momentum_meta(trades),
        "whale_meta":    _v2_whale_meta(trades),
        "funding_meta":  _v2_funding_meta(trades),
        "breakout_meta": _v2_breakout_meta(trades),
        "bot_panels": {
            "momentum": _v2_build_bot_panels(trades, None, "momentum"),
            "whale":    _v2_build_bot_panels(trades, None, "whale"),
            "funding":  _v2_build_bot_panels(trades, None, "funding"),
            "breakout": _v2_build_bot_panels(trades, None, "breakout"),
            "pair":     _v2_build_bot_panels(trades, None, "pair"),
            "reversal": _v2_build_bot_panels(trades, None, "reversal"),
        },
        "pair_meta":     _v2_pair_meta(trades),
        "reversal_meta": _v2_reversal_meta(trades),
        # J.5a: chart panels (empty when no executor available in test context)
        "chart_panels_root": _v2_build_all_chart_panels(None, trades),
        "projection":    _v2_projection(),
        "equity_curve_svg": _v2_equity_curve_svg(
            _v2_equity_series(trades, days=90)),
        "daily_pnl_svg":    _v2_daily_pnl_svg(
            _v2_daily_pnl_bars(trades, days=30)),
        "risk_metrics":      _v2_risk_metrics(_compute_metrics(trades)),
        "regime_expectancy": _v2_regime_expectancy(_compute_metrics(trades)),
    }
    ctx.update(overrides)
    return ctx


def _v2_why_silent(bot_class: str, data: Dict[str, Any]) -> dict | None:
    """Compute the one-sentence "why isn't this bot trading?" answer.

    Returns None when the bot is healthy and trading — the panel hides.
    Otherwise returns {"label", "detail", "kind"} with kind in
    {"silent" | "dormant" | "info"} for CSS class selection.
    """
    from collections import Counter

    if bot_class == "whale":
        try:
            from whale_config import WHALE_PAUSED
        except ImportError:
            WHALE_PAUSED = False
        if WHALE_PAUSED:
            return {
                "label":  "Paused by operator",
                "detail": "Peer-review consensus: 12/14 trades SL-hit. "
                          "Retire or redesign before re-enabling.",
                "kind":   "dormant",
            }
        return None

    if bot_class == "breakout":
        try:
            from breakout_config import BREAKOUT_PAUSED
        except ImportError:
            BREAKOUT_PAUSED = True
        if BREAKOUT_PAUSED:
            return {
                "label":  "Paused — pending backtest validation",
                "detail": "BREAKOUT_PAUSED=true. Enable per asset after "
                          "TradingView backtest passes PF≥1.8 over 5 years.",
                "kind":   "dormant",
            }
        # Live but no trades — Donchian breaks don't fire often
        bo_trades = [t for t in data.get("_trades_cache", [])
                     if t.get("bot") == "Breakout"]
        if not bo_trades:
            return {
                "label":  "Awaiting Donchian break",
                "detail": "Live. No N-bar high/low has printed with the "
                          "ADX/ATR regime gates passing since launch.",
                "kind":   "info",
            }
        return None

    if bot_class == "pair":
        try:
            from pair_config import PAIR_PAUSED
        except ImportError:
            PAIR_PAUSED = True
        if PAIR_PAUSED:
            return {
                "label":  "Paused — pending backtest validation",
                "detail": "PAIR_PAUSED=true. Enable after ETH/BTC z-score "
                          "backtest passes PF≥1.5 over 5 years.",
                "kind":   "dormant",
            }
        pair_trades = [t for t in data.get("_trades_cache", [])
                       if t.get("bot") == "Pair"]
        if not pair_trades:
            return {
                "label":  "Awaiting z-score extreme",
                "detail": "Live. ETH/BTC ratio hasn't hit |z|≥2 since launch.",
                "kind":   "info",
            }
        return None

    if bot_class == "reversal":
        try:
            from reversal_config import REVERSAL_PAUSED
        except ImportError:
            REVERSAL_PAUSED = True
        if REVERSAL_PAUSED:
            return {
                "label":  "Permanently deferred — no edge on Daily",
                "detail": "RSI(close)+extreme-reversal (Alex Carter spec). "
                          "After 6 backtest rounds + 2 real bugs fixed "
                          "(cumulative-VWAP, RSI-source-mismatch), strategy "
                          "fires signals but PF<1.0 on BTC/ETH 1D. Deferred "
                          "indefinitely; see plan Phase I.X.",
                "kind":   "dormant",
            }
        rev_trades = [t for t in data.get("_trades_cache", [])
                      if t.get("bot") == "Reversal"]
        if not rev_trades:
            return {
                "label":  "Awaiting RSI+extreme alignment",
                "detail": "Live. No bar has printed RSI(VWAP) outside 10/90 "
                          "with a 3× range capitulation candle and matching "
                          "dot polarity since launch.",
                "kind":   "info",
            }
        return None

    if bot_class == "funding":
        try:
            from funding_config import (
                FUNDING_PAUSED, FUNDING_UNIVERSE_MODE,
            )
        except ImportError:
            FUNDING_PAUSED = False
            FUNDING_UNIVERSE_MODE = "OI"
        if FUNDING_PAUSED:
            return {
                "label":  "Paused",
                "detail": "FUNDING_PAUSED=true — no new entries until cleared.",
                "kind":   "dormant",
            }
        # No funding trade has closed yet — explain the filters
        funding_trades = [t for t in data.get("_trades_cache", [])
                          if t.get("bot") == "Funding"]
        closed = [t for t in funding_trades
                  if t.get("exit_price") not in (None, 0, "0", "")]
        if not closed:
            return {
                "label":  "Awaiting first signal",
                "detail": (f"Universe mode = {FUNDING_UNIVERSE_MODE}. No funding "
                           f"extreme has met all filters (percentile, abs floor, "
                           f"OI, low-vol regime, trend, ±30min window) since launch."),
                "kind":   "info",
            }
        return None

    # Momentum: aggregate blocked_by across all monitored assets
    signal_status = data.get("signal_status") or {}
    if not signal_status:
        return None
    blocked_counts = Counter(
        info.get("blocked_by")
        for info in signal_status.values()
        if not info.get("would_enter") and info.get("blocked_by")
    )
    if not blocked_counts:
        return None
    total = len(signal_status)
    top_reason, top_count = blocked_counts.most_common(1)[0]
    from blocker_labels import blocker_label
    return {
        "label":  blocker_label(top_reason),
        "detail": f"{top_count}/{total} strategies blocked by this filter",
        "kind":   "silent",
    }


def _v2_sparkline_points(trades: List[dict], bot_label: str | None = None,
                          days: int = 30) -> List[float]:
    """Cumulative PnL over the last `days` calendar days, one point per day.

    If `bot_label` is None, aggregates across all bots (portfolio sparkline).
    Filters to closed trades only. Returns a list of cumulative PnL floats
    in chronological order — length up to `days` (fewer if there's no
    older history). Empty input → empty list.
    """
    from datetime import datetime, timedelta, timezone

    closed = [t for t in trades
              if t.get("exit_price") not in (None, 0, "0", "")
              and (bot_label is None or t.get("bot") == bot_label)]
    if not closed:
        return []

    by_day: dict[str, float] = {}
    for t in closed:
        d = (t.get("date_opened") or "")[:10]
        if not d:
            continue
        by_day[d] = by_day.get(d, 0.0) + float(t.get("net_pnl") or 0)

    if not by_day:
        return []

    # Build a contiguous N-day window ending today; missing days carry 0.
    now = datetime.now(timezone.utc).date()
    window = [(now - timedelta(days=i)) for i in range(days - 1, -1, -1)]
    daily = [by_day.get(d.isoformat(), 0.0) for d in window]

    # Trim leading days with no activity so the sparkline starts on first trade
    first_nonzero = next((i for i, v in enumerate(daily) if v != 0), 0)
    # But always include at least 2 points so the line draws
    if first_nonzero > 0:
        first_nonzero = max(0, first_nonzero - 1)
    daily = daily[first_nonzero:]

    cum = []
    running = 0.0
    for v in daily:
        running += v
        cum.append(round(running, 2))
    return cum


def _v2_sparkline_svg(points: List[float], width: int = 120, height: int = 24,
                       stroke_class: str = "spark__line",
                       label: str = "PnL trend") -> str:
    """Render a list of PnL points as inline SVG path. Returns "" if empty.

    Color is applied via CSS class — the sign of the last point picks
    .spark__line--up or .spark__line--down at render time in the caller.

    Accessibility: the SVG carries role="img" + an aria-label that
    summarizes the curve in plain text. The polyline itself is decorative
    (no individual data points are read).
    """
    if not points or len(points) < 2:
        return ""
    lo = min(points)
    hi = max(points)
    span = (hi - lo) or 1.0
    n = len(points)
    pad = 2
    inner_w = width - pad * 2
    inner_h = height - pad * 2
    coords = []
    for i, v in enumerate(points):
        x = pad + (i / (n - 1)) * inner_w
        y = pad + (1 - (v - lo) / span) * inner_h
        coords.append(f"{x:.1f},{y:.1f}")
    zero_y = None
    if lo < 0 < hi:
        zero_y = pad + (hi / span) * inner_h
    path = " ".join(coords)
    zero_line = (f'<line x1="{pad}" y1="{zero_y:.1f}" x2="{width - pad}" y2="{zero_y:.1f}" '
                 f'class="spark__zero"/>' if zero_y is not None else "")
    # Plain-text summary for screen readers
    last = points[-1]
    direction = ("up" if last > points[0]
                 else "down" if last < points[0]
                 else "flat")
    aria = (f"{label}: {direction} from {_v2_pnl_display(points[0])} "
            f"to {_v2_pnl_display(last)} over {n} days")
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" role="img" '
            f'aria-label="{aria}">'
            f'{zero_line}'
            f'<polyline class="{stroke_class}" points="{path}" '
            f'fill="none" stroke-width="1.25" stroke-linejoin="round" '
            f'stroke-linecap="round"/></svg>')


# ─── Equity curve + daily P/L (Phase D.7e) ─────────────────────────────────

def _v2_equity_series(trades: List[dict], days: int = 90) -> dict:
    """Build cumulative-PnL series for the Overview equity curve panel.

    Returns dict with `labels` (list of ISO dates, one per day) and
    `series` (list of 4: Portfolio aggregate + Momentum + Whale + Funding).
    Each series carries a CSS `modifier` for color theming.

    All series share the same X-axis so they can be overlaid; days with no
    trades carry the previous cumulative value (zero before any trade).
    """
    closed = [t for t in trades
              if t.get("exit_price") not in (None, 0, "0", "")]

    now = datetime.now(timezone.utc).date()
    window = [(now - timedelta(days=i)) for i in range(days - 1, -1, -1)]
    labels = [d.isoformat() for d in window]

    def _cum(filter_label):
        by_day: dict[str, float] = {}
        for t in closed:
            if filter_label is not None and t.get("bot") != filter_label:
                continue
            d = (t.get("date_opened") or "")[:10]
            if not d:
                continue
            by_day[d] = by_day.get(d, 0.0) + float(t.get("net_pnl") or 0)
        daily = [by_day.get(lbl, 0.0) for lbl in labels]
        out, running = [], 0.0
        for v in daily:
            running += v
            out.append(round(running, 2))
        return out

    series = [
        {"label": "Portfolio", "modifier": "aggregate", "values": _cum(None)},
        {"label": "Momentum",  "modifier": "momentum",  "values": _cum("Momentum")},
        {"label": "Whale",     "modifier": "whale",     "values": _cum("Whale")},
        {"label": "Funding",   "modifier": "funding",   "values": _cum("Funding")},
    ]

    # Trim leading days where every series is still zero — avoids a 60-day
    # flat-line lead-in that wastes the panel's horizontal space.
    first_nonzero = next(
        (i for i in range(len(labels))
         if any(s["values"][i] != 0 for s in series)),
        None,
    )
    if first_nonzero is not None and first_nonzero > 0:
        # Keep one day of zero on the left so the line starts at the baseline
        start = max(0, first_nonzero - 1)
        labels = labels[start:]
        series = [
            {**s, "values": s["values"][start:]}
            for s in series
        ]

    return {"labels": labels, "series": series}


def _fmt_dollar(v: float) -> str:
    """Compact $-formatter for chart axis labels."""
    if v == 0:
        return "$0"
    sign = "-" if v < 0 else ""
    av = abs(v)
    if av >= 1000:
        return f"{sign}${av/1000:.1f}k"
    return f"{sign}${av:.0f}"


def _fmt_date_short(iso_date: str) -> str:
    """`2026-06-05` → `06-05` (MM-DD only, no year clutter)."""
    return iso_date[5:] if len(iso_date) >= 10 else iso_date


def _v2_equity_curve_svg(series_data: dict, width: int = 720,
                          height: int = 220) -> str:
    """Render the 4-series equity curve as inline SVG with axis labels."""
    all_values = []
    for s in series_data["series"]:
        all_values.extend(s["values"])
    if not all_values:
        return ""

    vmin = min(0.0, min(all_values))
    vmax = max(0.0, max(all_values))
    if vmin == vmax:
        vmax = vmin + 1.0  # avoid div-by-zero on flat-zero data

    n = len(series_data["labels"])
    if n < 2:
        return ""

    # Axis padding — reserves room for labels around the plot area
    pad_left   = 52
    pad_right  = 12
    pad_top    = 8
    pad_bottom = 22

    plot_w = width  - pad_left - pad_right
    plot_h = height - pad_top  - pad_bottom

    span = vmax - vmin

    def _x(i):
        return pad_left + i / (n - 1) * plot_w

    def _y(v):
        return pad_top + plot_h - ((v - vmin) / span) * plot_h

    polylines = []
    for s in series_data["series"]:
        pts = " ".join(
            f"{_x(i):.1f},{_y(v):.1f}"
            for i, v in enumerate(s["values"])
        )
        is_aggregate = s["modifier"] == "aggregate"
        sw = 2.0 if is_aggregate else 1.25
        cls = f'equity-curve__series equity-curve__series--{s["modifier"]}'
        polylines.append(
            f'<polyline class="{cls}" points="{pts}" fill="none" '
            f'stroke-width="{sw}" stroke-linejoin="round" '
            f'stroke-linecap="round"/>'
        )

    zero_y = _y(0)
    zero_line = (
        f'<line class="equity-curve__zero" x1="{pad_left}" y1="{zero_y:.1f}" '
        f'x2="{width - pad_right}" y2="{zero_y:.1f}" stroke-width="1" '
        f'stroke-dasharray="3,5"/>'
    )

    # ── Y-axis labels (max, zero if in range, min) ──────────────────────
    y_label_x = pad_left - 6
    y_labels = [
        f'<text class="chart-axis chart-axis--y" x="{y_label_x}" '
        f'y="{pad_top + 4}" text-anchor="end">{_fmt_dollar(vmax)}</text>',
        f'<text class="chart-axis chart-axis--y" x="{y_label_x}" '
        f'y="{height - pad_bottom}" text-anchor="end">{_fmt_dollar(vmin)}</text>',
    ]
    if vmin < 0 < vmax:
        y_labels.append(
            f'<text class="chart-axis chart-axis--y" x="{y_label_x}" '
            f'y="{zero_y + 4:.1f}" text-anchor="end">$0</text>'
        )

    # ── X-axis labels (start, middle, end dates) ────────────────────────
    labels = series_data["labels"]
    mid_idx = n // 2
    x_y = height - 6
    x_labels = [
        f'<text class="chart-axis chart-axis--x" x="{_x(0):.1f}" '
        f'y="{x_y}" text-anchor="start">{_fmt_date_short(labels[0])}</text>',
        f'<text class="chart-axis chart-axis--x" x="{_x(mid_idx):.1f}" '
        f'y="{x_y}" text-anchor="middle">{_fmt_date_short(labels[mid_idx])}</text>',
        f'<text class="chart-axis chart-axis--x" x="{_x(n - 1):.1f}" '
        f'y="{x_y}" text-anchor="end">{_fmt_date_short(labels[-1])}</text>',
    ]

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
        f'width="100%" height="100%" '
        f'role="img" aria-label="{n}-day cumulative PnL curves">'
        f'{zero_line}'
        f'{"".join(polylines)}'
        f'{"".join(y_labels)}'
        f'{"".join(x_labels)}'
        f'</svg>'
    )


def _v2_daily_pnl_bars(trades: List[dict], days: int = 30) -> List[dict]:
    """Aggregate closed-trade PnL per day for the last N days.

    Returns a list of {date, pnl} dicts, chronological, length == days.
    """
    now = datetime.now(timezone.utc).date()
    window = [(now - timedelta(days=i)) for i in range(days - 1, -1, -1)]

    by_day: dict[str, float] = {}
    for t in trades:
        if t.get("exit_price") in (None, 0, "0", ""):
            continue
        d = (t.get("date_opened") or "")[:10]
        if not d:
            continue
        by_day[d] = by_day.get(d, 0.0) + float(t.get("net_pnl") or 0)

    return [
        {"date": d.isoformat(),
         "pnl":  round(by_day.get(d.isoformat(), 0.0), 2)}
        for d in window
    ]


def _v2_daily_pnl_svg(bars: List[dict], width: int = 720,
                       height: int = 140) -> str:
    """Render daily P/L as a green-up / red-down bar chart with axis labels."""
    if not bars:
        return ""

    values = [b["pnl"] for b in bars]
    vmax = max((abs(v) for v in values), default=0.0) or 1.0
    n = len(bars)

    # Axis padding for labels
    pad_left   = 52
    pad_right  = 12
    pad_top    = 8
    pad_bottom = 22
    plot_w = width  - pad_left - pad_right
    plot_h = height - pad_top  - pad_bottom

    slot = plot_w / n
    bar_w = slot * 0.7
    bar_gap = slot * 0.15
    zero_y = pad_top + plot_h / 2

    rects = []
    for i, b in enumerate(bars):
        x = pad_left + i * slot + bar_gap
        if b["pnl"] >= 0:
            h = (b["pnl"] / vmax) * (plot_h / 2)
            y = zero_y - h
            cls = "daily-bar daily-bar--up"
        else:
            h = (abs(b["pnl"]) / vmax) * (plot_h / 2)
            y = zero_y
            cls = "daily-bar daily-bar--down"
        if h < 0.5 and b["pnl"] == 0:
            continue
        rects.append(
            f'<rect class="{cls}" x="{x:.1f}" y="{y:.1f}" '
            f'width="{bar_w:.1f}" height="{max(h, 0.5):.1f}"/>'
        )

    zero_line = (
        f'<line class="daily-pnl__zero" x1="{pad_left}" y1="{zero_y:.1f}" '
        f'x2="{width - pad_right}" y2="{zero_y:.1f}" stroke-width="1"/>'
    )

    # ── Y-axis labels (max+, $0, max-) ──────────────────────────────────
    y_label_x = pad_left - 6
    y_labels = [
        f'<text class="chart-axis chart-axis--y" x="{y_label_x}" '
        f'y="{pad_top + 4}" text-anchor="end">{_fmt_dollar(vmax)}</text>',
        f'<text class="chart-axis chart-axis--y" x="{y_label_x}" '
        f'y="{zero_y + 4:.1f}" text-anchor="end">$0</text>',
        f'<text class="chart-axis chart-axis--y" x="{y_label_x}" '
        f'y="{height - pad_bottom}" text-anchor="end">{_fmt_dollar(-vmax)}</text>',
    ]

    # ── X-axis labels (start, end dates) ────────────────────────────────
    x_y = height - 6
    first_x = pad_left + bar_gap
    last_x  = pad_left + (n - 1) * slot + bar_gap + bar_w
    x_labels = [
        f'<text class="chart-axis chart-axis--x" x="{first_x:.1f}" '
        f'y="{x_y}" text-anchor="start">{_fmt_date_short(bars[0]["date"])}</text>',
        f'<text class="chart-axis chart-axis--x" x="{last_x:.1f}" '
        f'y="{x_y}" text-anchor="end">{_fmt_date_short(bars[-1]["date"])}</text>',
    ]

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
        f'width="100%" height="100%" '
        f'role="img" aria-label="Daily PnL last {n} days">'
        f'{zero_line}'
        f'{"".join(rects)}'
        f'{"".join(y_labels)}'
        f'{"".join(x_labels)}'
        f'</svg>'
    )


# ─── Trend computation (Phase D.7d) ─────────────────────────────────────────

def _v2_trend_glyph(direction: str) -> str:
    """Map a trend direction to its display glyph."""
    return {"up": "▲", "down": "▼"}.get(direction, "—")


def _v2_trend(trades: List[dict], bot_label: str, metric: str,
              days: int = 30) -> dict:
    """Compare trailing N-day window of a metric against the prior N-day window.

    metric: "net_pnl" or "win_rate".
    Returns {"direction": "up"|"down"|"flat", "delta": float, "available": bool}.

    "available" is False only when neither window has any closed trades for
    this bot — in that case direction is "flat" and the caller can hide the
    chip.
    """
    now = datetime.now(timezone.utc)
    cutoff_now   = now - timedelta(days=days)
    cutoff_prior = now - timedelta(days=days * 2)

    def _parse(t):
        s = (t.get("date_closed") or t.get("date_opened") or "").strip()
        if not s:
            return None
        # Production writes via datetime.now().isoformat() — ISO 8601 with
        # 'T' separator and microseconds, sometimes with timezone suffix.
        # Test fixtures use space-separated. fromisoformat handles both.
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        # Fallback for unusual legacy formats
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    bot_trades = [t for t in trades if t.get("bot") == bot_label]
    closed = [t for t in bot_trades
              if t.get("exit_price") not in (None, 0, "0", "")]

    current, prior = [], []
    for t in closed:
        dt = _parse(t)
        if dt is None:
            continue
        if dt >= cutoff_now:
            current.append(t)
        elif dt >= cutoff_prior:
            prior.append(t)

    if not current and not prior:
        return {"direction": "flat", "delta": 0.0, "available": False}

    def _value(window):
        if not window:
            return None
        if metric == "net_pnl":
            return sum(float(t.get("net_pnl") or 0) for t in window)
        if metric == "win_rate":
            wins = sum(1 for t in window
                       if (t.get("result") or "").upper() == "WIN")
            return (wins / len(window) * 100.0) if window else 0.0
        return 0.0

    cur = _value(current)
    pri = _value(prior)

    # First-signal: prior empty, current populated → infer from sign/value.
    if pri is None:
        if cur is None or cur == 0:
            return {"direction": "flat", "delta": 0.0, "available": True}
        return {"direction": "up" if cur > 0 else "down",
                "delta": cur, "available": True}
    # Current empty, prior populated → metric has gone silent; show flat.
    if cur is None:
        return {"direction": "flat", "delta": 0.0, "available": True}

    delta = cur - pri
    if delta > 0:
        direction = "up"
    elif delta < 0:
        direction = "down"
    else:
        direction = "flat"
    return {"direction": direction, "delta": delta, "available": True}


def _v2_projection() -> dict:
    """Wrap _compute_yearly_projection() with display-formatted fields.

    Returns dict with `rows` (per-strategy + whale), `total_annual`,
    `total_trades_per_year`, and the pre-formatted display strings the
    template renders.
    """
    proj = _compute_yearly_projection()
    rows = proj.get("rows", [])
    total_annual = proj.get("total_annual", 0.0)
    total_trades = proj.get("total_trades_per_year", 0.0)
    starting_capital = INITIAL_CAPITAL
    annual_pct = (total_annual / starting_capital * 100) if starting_capital > 0 else 0

    def _fmt(row):
        return {
            **row,
            "annual_pnl_live_display": _v2_pnl_display(row.get("annual_pnl_live", 0.0)),
            "pf_display":              f"{row.get('pf', 0):.2f}",
            "annual_pct_display":      f"{row.get('annual_pct_backtest', 0):+.1f}%",
            "trades_per_year_display": f"{row.get('trades_per_year', 0):.1f}",
            "dd_display":              f"{row.get('dd_pct', 0):.1f}%",
            "is_whale":                row.get("is_whale", False),
        }

    return {
        "rows":                       [_fmt(r) for r in rows],
        "starting_capital":           starting_capital,
        "starting_capital_display":   f"${starting_capital:,.0f}",
        "live_notional":              proj.get("live_notional", 0.0),
        "live_notional_display":      f"${proj.get('live_notional', 0):,.0f}",
        "total_annual":               total_annual,
        "total_annual_display":       _v2_pnl_display(total_annual),
        "annual_pct":                 annual_pct,
        "annual_pct_display":         f"{annual_pct:+.2f}%",
        "total_trades_per_year":      total_trades,
        "total_trades_display":       f"{total_trades:.1f}",
    }


def _v2_whale_meta(trades: List[dict]) -> dict:
    """Whale-specific metadata for the Whale tab — paused state + post-mortem."""
    try:
        from whale_config import WHALE_PAUSED
    except ImportError:
        WHALE_PAUSED = False
    whale_trades = [t for t in trades if t.get("bot") == "Whale"]
    closed = [t for t in whale_trades
              if t.get("exit_price") not in (None, 0, "0", "")]
    pnl_list = [float(t.get("net_pnl") or 0) for t in closed]
    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p < 0]
    return {
        "paused":            bool(WHALE_PAUSED),
        "pause_reason":      "peer-review consensus: 12/14 trades SL-hit; "
                             "retire or redesign before re-enabling",
        "closed_count":      len(closed),
        "wins":              len(wins),
        "losses":            len(losses),
        "flats":             len(closed) - len(wins) - len(losses),
        "win_rate_display":  (f"{len(wins) / len(closed) * 100:.1f}%"
                              if closed else "—"),
        "net_pnl_display":   _v2_pnl_display(sum(pnl_list)),
        "best_display":      _v2_pnl_display(max(pnl_list) if pnl_list else 0),
        "worst_display":     _v2_pnl_display(min(pnl_list) if pnl_list else 0),
        "avg_loss_display":  _v2_pnl_display(
                                sum(losses) / len(losses) if losses else 0),
    }


def _v2_funding_meta(trades: List[dict]) -> dict:
    """Funding-specific metadata for the Funding tab — config + state."""
    try:
        from funding_config import (
            FUNDING_PAUSED, FUNDING_UNIVERSE_MODE, FUNDING_UNIVERSE_MIN_OI_USD,
            FUNDING_PERCENTILE_THRESHOLD, FUNDING_ABSOLUTE_FLOOR,
            FUNDING_MIN_OI_USD, FUNDING_EXECUTION_WINDOW_MINUTES,
            FUNDING_FIXING_HOURS_UTC,
            FUNDING_ALLOW_LONG_FADE, FUNDING_ALLOW_SHORT_FADE,
            FUNDING_MARGIN_USD, FUNDING_LEVERAGE,
            FUNDING_REQUIRE_LOW_VOL,
        )
        config_available = True
    except ImportError:
        FUNDING_PAUSED = False
        FUNDING_UNIVERSE_MODE = "OI"
        FUNDING_UNIVERSE_MIN_OI_USD = 20_000_000
        FUNDING_PERCENTILE_THRESHOLD = 97.0
        FUNDING_ABSOLUTE_FLOOR = 0.0005
        FUNDING_MIN_OI_USD = 20_000_000
        FUNDING_EXECUTION_WINDOW_MINUTES = 30
        FUNDING_FIXING_HOURS_UTC = (0, 8, 16)
        FUNDING_ALLOW_LONG_FADE = True
        FUNDING_ALLOW_SHORT_FADE = True
        FUNDING_MARGIN_USD = 25.0
        FUNDING_LEVERAGE = 10
        FUNDING_REQUIRE_LOW_VOL = True
        config_available = False

    funding_trades = [t for t in trades if t.get("bot") == "Funding"]
    closed = [t for t in funding_trades
              if t.get("exit_price") not in (None, 0, "0", "")]
    pnl_list = [float(t.get("net_pnl") or 0) for t in closed]

    return {
        "config_available":   config_available,
        "paused":              bool(FUNDING_PAUSED),
        "universe_mode":       FUNDING_UNIVERSE_MODE,
        "universe_min_oi_m":   FUNDING_UNIVERSE_MIN_OI_USD / 1_000_000,
        "percentile":          float(FUNDING_PERCENTILE_THRESHOLD),
        "absolute_floor_pct":  float(FUNDING_ABSOLUTE_FLOOR) * 100,
        "min_oi_m":            FUNDING_MIN_OI_USD / 1_000_000,
        "window_minutes":      int(FUNDING_EXECUTION_WINDOW_MINUTES),
        "fixing_hours":        list(FUNDING_FIXING_HOURS_UTC),
        "allow_long_fade":     bool(FUNDING_ALLOW_LONG_FADE),
        "allow_short_fade":    bool(FUNDING_ALLOW_SHORT_FADE),
        "require_low_vol":     bool(FUNDING_REQUIRE_LOW_VOL),
        "margin_usd":          float(FUNDING_MARGIN_USD),
        "leverage":            int(FUNDING_LEVERAGE),
        "notional_usd":        float(FUNDING_MARGIN_USD) * int(FUNDING_LEVERAGE),
        "closed_count":        len(closed),
        "win_rate_display":    (f"{sum(1 for p in pnl_list if p > 0) / len(pnl_list) * 100:.1f}%"
                                if pnl_list else "—"),
        "net_pnl_display":     _v2_pnl_display(sum(pnl_list)),
    }


_BOT_CLASS_TO_LABEL = {
    "momentum": "Momentum",
    "whale":    "Whale",
    "funding":  "Funding",
    "breakout": "Breakout",
    "pair":     "Pair",
    "reversal": "Reversal",
}


def _v2_open_positions_for_bot(state: dict, bot_class: str) -> List[dict]:
    """Filter state['positions'] to entries owned by bot_class.

    Returns a list of dicts with display-formatted fields the
    bot_positions_panel template consumes.
    """
    from position_manager import _bot_of_key
    positions = (state or {}).get("positions", {}) or {}
    out = []
    for state_key, pos in positions.items():
        if _bot_of_key(state_key) != bot_class:
            continue
        direction = pos.get("direction", "LONG")
        entry = float(pos.get("entry_price") or 0)
        qty = float(pos.get("quantity") or 0)
        out.append({
            "state_key":      state_key,
            "symbol":         pos.get("symbol", state_key),
            "direction":      direction,
            "direction_class": "is-up" if direction == "LONG" else "is-down",
            "entry_display":  f"{entry:,.4f}" if entry < 1 else f"{entry:,.2f}",
            "qty_display":    f"{qty:,.6f}".rstrip("0").rstrip(".") or "0",
            "strategy":       pos.get("strategy", ""),
            "entry_reason":   pos.get("entry_reason", ""),
        })
    return out


def _v2_closed_trades_for_bot(trades: List[dict], bot_class: str,
                                limit: int = 50) -> List[dict]:
    """Filter trades to closed entries owned by bot_class, newest first.

    Each row gets display-formatted fields matching the trade-log table.
    """
    label = _BOT_CLASS_TO_LABEL.get(bot_class, bot_class.capitalize())
    matching = [
        t for t in (trades or [])
        if t.get("bot") == label
        and t.get("exit_price") not in (None, 0, "0", "")
    ]
    # Sort newest first by id (fallback to date_closed)
    def _sort_key(t):
        return (t.get("id") or 0, t.get("date_closed") or "")
    matching.sort(key=_sort_key, reverse=True)
    matching = matching[: max(1, int(limit))]

    out = []
    for t in matching:
        net_pnl = float(t.get("net_pnl") or 0)
        out.append({
            "id":              t.get("id"),
            "date_closed":     (t.get("date_closed") or "")[:16].replace("T", " "),
            "symbol":          t.get("symbol", ""),
            "direction":       t.get("direction", ""),
            "strategy":        t.get("strategy", ""),
            "entry_price":     t.get("entry_price"),
            "exit_price":      t.get("exit_price"),
            "net_pnl":         net_pnl,
            "net_pnl_display": _v2_pnl_display(net_pnl),
            "result":          t.get("result", ""),
            "result_class":    ("is-up" if net_pnl > 0
                                 else ("is-down" if net_pnl < 0 else "is-flat")),
            "exit_reason":     t.get("exit_reason", ""),
        })
    return out


def _v2_kpis_for_bot(trades: List[dict], bot_class: str) -> dict:
    """Scoped Sortino / Calmar / Streak / Recovery / Max DD for one bot's
    trades. Uses metrics.py functions on the bot's filtered pnls."""
    import metrics as _m
    label = _BOT_CLASS_TO_LABEL.get(bot_class, bot_class.capitalize())
    closed = [
        t for t in (trades or [])
        if t.get("bot") == label
        and t.get("exit_price") not in (None, 0, "0", "")
    ]
    pnls = [float(t.get("net_pnl") or 0) for t in closed]

    if not pnls:
        return {
            "closed_count":     0,
            "sortino_display":  "—",
            "calmar_display":   "—",
            "max_dd_display":   "—",
            "streak_display":   "—",
            "streak_class":     "is-flat",
            "recovery_display": "—",
        }

    # Reuse the existing display formatter from _v2_risk_metrics
    def _ratio(v):
        if v is None or v == 0:
            return "—"
        if v >= 999 or v <= -999:
            return "∞"
        return f"{v:+.2f}"

    equity = [INITIAL_CAPITAL]
    running = INITIAL_CAPITAL
    for p in pnls:
        running += p
        equity.append(running)

    sortino = _m.sortino(pnls, trades_per_year=max(1, len(pnls) * 12))
    calmar  = _m.calmar(pnls, initial_equity=INITIAL_CAPITAL, days=90)
    max_dd  = _m.max_drawdown(equity)
    streak_count, streak_type = _m.consecutive_streak(pnls)
    recovery = _m.recovery_factor(pnls, initial_equity=INITIAL_CAPITAL)

    streak_class = (
        "is-up" if streak_type == "WIN"
        else "is-down" if streak_type == "LOSS"
        else "is-flat"
    )
    streak_display = "—" if streak_count == 0 else f"{streak_count} {streak_type}"

    return {
        "closed_count":     len(closed),
        "sortino_display":  _ratio(round(sortino, 2)),
        "calmar_display":   _ratio(calmar),
        "max_dd_display":   f"{max_dd:.1f}%" if max_dd else "—",
        "streak_display":   streak_display,
        "streak_class":     streak_class,
        "recovery_display": _ratio(recovery),
    }


def _v2_build_bot_panels(trades: List[dict], state: dict | None,
                          bot_class: str) -> dict:
    """Bundle positions + recent closed trades + scoped KPIs for one bot.

    Templates consume this as `bot_panels.{positions,trades,kpis}`.
    """
    return {
        "positions": _v2_open_positions_for_bot(state or {}, bot_class),
        "trades":    _v2_closed_trades_for_bot(trades or [], bot_class, limit=25),
        "kpis":      _v2_kpis_for_bot(trades or [], bot_class),
    }


# ─── J.5a — kline cache + per-asset chart-data builder ────────────────────

_kline_cache: dict[tuple[str, str, int], tuple[float, list]] = {}
_KLINE_CACHE_TTL_S = 300.0  # 5 minutes


def _kline_cache_clear() -> None:
    """Clear the per-process kline cache (tests + manual recompute)."""
    _kline_cache.clear()


def _v2_fetch_klines_cached(executor, symbol: str, interval: str,
                              count: int = 200):
    """TTL-cached wrapper around executor.get_klines.

    Returns the raw WEEX positional kline rows (list of lists). Cache key is
    (symbol, interval, count); TTL is 5 min so a dashboard rebuild burst
    only hits WEEX once per asset/timeframe.
    """
    import time as _time
    key = (symbol, interval, int(count))
    now = _time.time()
    hit = _kline_cache.get(key)
    if hit is not None and (now - hit[0]) < _KLINE_CACHE_TTL_S:
        return hit[1]
    try:
        rows = executor.get_klines(symbol, interval, count) or []
    except Exception as e:  # noqa: BLE001
        logger.warning("kline fetch failed for %s %s: %s", symbol, interval, e)
        rows = []
    _kline_cache[key] = (now, rows)
    return rows


def _v2_kline_rows_to_df(rows: list):
    """Convert WEEX positional kline rows → pandas DataFrame with OHLCV.

    Row schema: [open_time_ms, open, high, low, close, volume, close_time_ms,
                 ...]. Returns empty DF if rows is empty or pandas missing.
    """
    try:
        import pandas as pd  # noqa: F401
    except ImportError:
        return None
    import pandas as pd  # local import
    if not rows:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close",
                                       "volume"])
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "trades", "tbb", "tbq",
    ][: len(rows[0])])
    # TWLC expects seconds, WEEX returns ms
    df["time"] = (df["open_time"].astype("int64") // 1000)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    return df[["time", "open", "high", "low", "close", "volume"]]


def _v2_ema(series, length: int):
    """Pandas-only EMA so the chart helper has no pandas-ta dependency."""
    return series.ewm(span=length, adjust=False).mean()


def _v2_parse_ts_to_unix(ts) -> int | None:
    """Parse a trade's date_opened / date_closed value to a unix seconds int.

    Accepts ISO strings (full or date-only) and ints/floats already in seconds
    or milliseconds. Returns None when unparseable.
    """
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        v = float(ts)
        # heuristic: > 10^11 → milliseconds
        return int(v / 1000) if v > 1e11 else int(v)
    s = str(ts).strip()
    if not s:
        return None
    # Try ISO 8601 (full datetime)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        pass
    # Try date-only
    try:
        dt = datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return None


def _v2_trade_markers_for_asset(trades: list, bot_class: str,
                                  symbol: str,
                                  candle_window: tuple[int, int]) -> list:
    """Build TWLC marker objects for closed trades on (bot_class, symbol).

    candle_window is (first_unix_s, last_unix_s) inclusive. Markers outside
    that window are skipped so we don't push markers into the chart's left
    margin. Returns a flat list of {time, position, color, shape, text}.
    """
    label = _BOT_CLASS_TO_LABEL.get(bot_class, bot_class.capitalize())
    first_ts, last_ts = candle_window
    out: list[dict] = []
    for t in trades or []:
        if (t.get("bot") or "") != label:
            continue
        if (t.get("symbol") or "") != symbol:
            continue
        direction = (t.get("direction") or "").upper()
        entry_ts = _v2_parse_ts_to_unix(t.get("date_opened"))
        exit_ts  = _v2_parse_ts_to_unix(t.get("date_closed"))
        net_pnl  = float(t.get("net_pnl") or 0)
        entry_px = t.get("entry_price")
        exit_px  = t.get("exit_price")

        if entry_ts is not None and first_ts <= entry_ts <= last_ts:
            is_long = direction == "LONG"
            out.append({
                "time":     entry_ts,
                "position": "belowBar" if is_long else "aboveBar",
                "color":    "#57cb95" if is_long else "#e85a4c",
                "shape":    "arrowUp" if is_long else "arrowDown",
                "text":     f"{direction} @ {entry_px}",
            })
        if (exit_ts is not None and exit_px not in (None, "", 0, "0")
                and first_ts <= exit_ts <= last_ts):
            won = net_pnl > 0
            out.append({
                "time":     exit_ts,
                "position": "aboveBar" if direction == "LONG" else "belowBar",
                "color":    "#57cb95" if won else "#e85a4c",
                "shape":    "circle",
                "text":     f"Exit {exit_px} ({'+' if won else ''}{net_pnl:.2f})",
            })
    # TWLC requires markers sorted by time ascending
    out.sort(key=lambda m: m["time"])
    return out


def _v2_overlays_for_bot(bot_class: str, df, cfg: dict) -> list[dict]:
    """Return the bot-specific indicator overlays as TWLC line-series dicts.

    Each overlay is {name, color, data: [{time, value}, ...]}. NaN values
    are stripped because TWLC rejects them.
    """
    if df is None or len(df) == 0:
        return []
    overlays: list[dict] = []
    close = df["close"]
    times = df["time"]

    def _line(name: str, color: str, series) -> dict:
        data = [{"time": int(t), "value": float(v)}
                for t, v in zip(times, series) if v == v]  # NaN-safe
        return {"name": name, "color": color, "data": data}

    if bot_class == "pair":
        # Pair chart shows the ratio + 30d rolling mean + ±2σ bands,
        # not OHLC. The ratio Series is already in df["close"] (set up
        # by _v2_asset_chart_data's pair branch).
        z_window = int(cfg.get("z_window", 30))
        mean = close.rolling(z_window, min_periods=max(2, z_window // 2)).mean()
        std  = close.rolling(z_window, min_periods=max(2, z_window // 2)).std()
        entry_z = float(cfg.get("entry_z", 2.0))
        overlays.append(_line("Ratio", "#5fa8e5", close))
        overlays.append(_line(f"Mean ({z_window}d)", "#d4ad58", mean))
        overlays.append(_line(f"+{entry_z}σ", "#e85a4c", mean + entry_z * std))
        overlays.append(_line(f"−{entry_z}σ", "#57cb95", mean - entry_z * std))
        return overlays

    if bot_class == "momentum":
        ema_fast = int(cfg.get("ema_fast", 20))
        ema_slow = int(cfg.get("ema_slow", 50))
        overlays.append(_line(f"EMA{ema_fast}", "#5fa8e5",
                                _v2_ema(close, ema_fast)))
        overlays.append(_line(f"EMA{ema_slow}", "#d4ad58",
                                _v2_ema(close, ema_slow)))
    elif bot_class == "breakout":
        entry_n = int(cfg.get("donchian_period", 55))
        exit_n  = int(cfg.get("donchian_exit_period", 20))
        high = df["high"]; low = df["low"]
        entry_upper = high.rolling(entry_n, min_periods=entry_n).max()
        entry_lower = low.rolling(entry_n, min_periods=entry_n).min()
        exit_upper  = high.rolling(exit_n,  min_periods=exit_n ).max()
        exit_lower  = low.rolling(exit_n,   min_periods=exit_n ).min()
        overlays.append(_line(f"Donchian-{entry_n} upper", "#5fa8e5",
                                entry_upper))
        overlays.append(_line(f"Donchian-{entry_n} lower", "#5fa8e5",
                                entry_lower))
        overlays.append(_line(f"Donchian-{exit_n} upper",  "#d4ad58",
                                exit_upper))
        overlays.append(_line(f"Donchian-{exit_n} lower",  "#d4ad58",
                                exit_lower))
    return overlays


def _v2_pair_ratio_df(executor, long_symbol: str, short_symbol: str,
                       interval: str, count: int = 200):
    """Build a DataFrame with time + ratio (long_close / short_close).

    Returns None when either leg's klines are missing or pandas is absent.
    The result is shaped like a candle df (open=high=low=close=ratio) so
    the same overlay logic + marker pipeline works.
    """
    try:
        import pandas as pd  # noqa: F401
    except ImportError:
        return None
    import pandas as pd  # local
    long_rows  = _v2_fetch_klines_cached(executor, long_symbol,  interval, count)
    short_rows = _v2_fetch_klines_cached(executor, short_symbol, interval, count)
    long_df  = _v2_kline_rows_to_df(long_rows)
    short_df = _v2_kline_rows_to_df(short_rows)
    if (long_df is None or short_df is None
            or len(long_df) == 0 or len(short_df) == 0):
        return None
    merged = long_df[["time", "close"]].merge(
        short_df[["time", "close"]], on="time", suffixes=("_l", "_s"))
    if len(merged) == 0:
        return None
    merged["ratio"] = merged["close_l"] / merged["close_s"]
    df = pd.DataFrame({
        "time":  merged["time"],
        "open":  merged["ratio"], "high": merged["ratio"],
        "low":   merged["ratio"], "close": merged["ratio"],
        "volume": 0.0,
    })
    return df


def _v2_asset_chart_data(executor, bot_class: str, asset_name: str,
                          cfg: dict, trades: list) -> dict:
    """Build the TWLC chart-data dict for one (bot, asset) tab panel.

    Schema (matches what initAssetChart in dashboard.js consumes):
      {
        "candles":  [{time, open, high, low, close}, ...],
        "overlays": [{name, color, data: [{time, value}, ...]}, ...],
        "markers":  [{time, position, color, shape, text}, ...],
      }
    Pair charts return empty `candles` (ratio is a line, not OHLC); the
    first overlay is the ratio itself and JS attaches markers to it.

    Empty kline data returns all-empty arrays so the JS can render an
    empty-state chart without crashing.
    """
    if bot_class == "pair":
        long_symbol  = cfg.get("long_symbol",  "ETHUSDT")
        short_symbol = cfg.get("short_symbol", "BTCUSDT")
        interval     = cfg.get("interval", "1d")
        df = _v2_pair_ratio_df(executor, long_symbol, short_symbol, interval)
        if df is None or len(df) == 0:
            return {"candles": [], "overlays": [], "markers": []}
        overlays = _v2_overlays_for_bot("pair", df, cfg)
        window = (int(df["time"].iloc[0]), int(df["time"].iloc[-1]))
        # Pair trades store the long-leg symbol; filter on that.
        markers = _v2_trade_markers_for_asset(
            trades or [], "pair", long_symbol, window)
        return {"candles": [], "overlays": overlays, "markers": markers}

    symbol   = cfg.get("symbol", "")
    interval = cfg.get("interval", "4h")
    rows = _v2_fetch_klines_cached(executor, symbol, interval, 200)
    df = _v2_kline_rows_to_df(rows)
    if df is None or len(df) == 0:
        return {"candles": [], "overlays": [], "markers": []}

    candles = [
        {"time": int(r.time), "open": float(r.open), "high": float(r.high),
         "low":  float(r.low), "close": float(r.close)}
        for r in df.itertuples(index=False)
    ]
    overlays = _v2_overlays_for_bot(bot_class, df, cfg)
    window = (int(df["time"].iloc[0]), int(df["time"].iloc[-1]))
    markers = _v2_trade_markers_for_asset(
        trades or [], bot_class, symbol, window)
    return {"candles": candles, "overlays": overlays, "markers": markers}


def _v2_assets_for_bot(bot_class: str) -> dict[str, dict]:
    """Return the configured asset dict for one bot (name → cfg).

    Used by the chart-panel builder to enumerate which assets get charts.
    Falls back to {} when the bot's config module is missing or its
    universe is dynamic (whale).
    """
    try:
        if bot_class == "momentum":
            from config import ASSETS
            return dict(ASSETS)
        if bot_class == "breakout":
            from breakout_config import BREAKOUT_ASSETS
            return dict(BREAKOUT_ASSETS)
        if bot_class == "reversal":
            from reversal_config import REVERSAL_ASSETS
            return dict(REVERSAL_ASSETS)
        if bot_class == "pair":
            from pair_config import (
                PAIR_LONG_SYMBOL, PAIR_SHORT_SYMBOL,
                PAIR_INTERVAL, PAIR_CONFIG,
            )
            return {
                "ETHBTC": {
                    "symbol":       PAIR_LONG_SYMBOL,  # display only
                    "long_symbol":  PAIR_LONG_SYMBOL,
                    "short_symbol": PAIR_SHORT_SYMBOL,
                    "interval":     PAIR_INTERVAL,
                    "z_window":     PAIR_CONFIG.get("z_window", 30),
                    "entry_z":      PAIR_CONFIG.get("entry_z",  2.0),
                },
            }
    except ImportError:
        return {}
    # whale (dynamic universe), funding (dynamic — no log data yet)
    return {}


def _v2_build_chart_panels_for_bot(executor, trades: list,
                                     bot_class: str,
                                     max_assets: int = 5) -> list[dict]:
    """Build chart-panel entries for one bot — one per configured asset.

    Returns [{asset_name, chart_id, chart_data, symbol, interval}, ...]
    sorted by recent trade activity (most-traded first), capped to
    max_assets. Returns [] when executor is None (test context) or the
    bot has no configured assets (whale/funding).
    """
    assets = _v2_assets_for_bot(bot_class)
    if not assets:
        return []
    if executor is None:
        # Test context: emit dropdown options but no chart data
        return [
            {"asset_name": name,
             "symbol":     assets[name].get("symbol", ""),
             "interval":   assets[name].get("interval", ""),
             "chart_id":   f"{bot_class}-{name.replace('_', '-')}",
             "chart_data": {"candles": [], "overlays": [], "markers": []}}
            for name in list(assets.keys())[:max_assets]
        ]

    # Rank assets by recent trade count for this bot
    label = _BOT_CLASS_TO_LABEL.get(bot_class, bot_class.capitalize())
    counts: dict[str, int] = {name: 0 for name in assets}
    for t in trades or []:
        if (t.get("bot") or "") != label:
            continue
        sym = t.get("symbol") or ""
        for name, cfg in assets.items():
            if cfg.get("symbol") == sym:
                counts[name] += 1
                break
    ranked = sorted(assets.keys(),
                    key=lambda n: (-counts[n], n))[:max_assets]

    out: list[dict] = []
    for name in ranked:
        cfg = assets[name]
        try:
            data = _v2_asset_chart_data(executor, bot_class, name, cfg,
                                          trades)
        except Exception as e:  # noqa: BLE001
            logger.warning("chart data build failed for %s/%s: %s",
                            bot_class, name, e)
            data = {"candles": [], "overlays": [], "markers": []}
        out.append({
            "asset_name": name,
            "symbol":     cfg.get("symbol", ""),
            "interval":   cfg.get("interval", ""),
            "chart_id":   f"{bot_class}-{name.replace('_', '-')}",
            "chart_data": data,
        })
    return out


def _v2_build_all_chart_panels(executor, trades: list) -> dict:
    """Return per-bot chart-panel lists for templates.

    Shape: {bot_class: [panel_dict, ...]}. Bots without configured asset
    universes get [] entries — the template hides the chart section in
    that case.
    """
    return {
        "momentum": _v2_build_chart_panels_for_bot(executor, trades,
                                                     "momentum"),
        "breakout": _v2_build_chart_panels_for_bot(executor, trades,
                                                     "breakout"),
        "whale":    [],
        "funding":  [],
        "pair":     _v2_build_chart_panels_for_bot(executor, trades,
                                                     "pair", max_assets=1),
        "reversal": _v2_build_chart_panels_for_bot(executor, trades,
                                                     "reversal"),
    }


def _v2_render_asset_chart_panel(chart_id: str, chart_data: dict,
                                   height_px: int = 400) -> str:
    """Render a TradingView Lightweight Charts container + inlined data + init.

    Emits three elements:
      1. <div id="chart-{id}" class="asset-chart" style="height:{H}px">
      2. <script type="application/json" id="chartdata-{id}">…JSON…</script>
      3. <script>initAssetChart("{id}", JSON.parse(...))</script>

    The JS-side `initAssetChart` function lives in dashboard.js (Phase J.2)
    and is responsible for constructing a chart, adding the candle series,
    looping over overlays as line series, and applying markers.

    chart_id is sanitized to [a-zA-Z0-9_] only — injection-safe.
    """
    import json
    import re
    safe_id = re.sub(r"[^a-zA-Z0-9_]", "", chart_id) or "anon"
    data_json = json.dumps(chart_data, separators=(", ", ": "))
    return (
        f'<div id="chart-{safe_id}" class="asset-chart" '
        f'style="height:{int(height_px)}px"></div>\n'
        f'<script type="application/json" id="chartdata-{safe_id}">'
        f'{data_json}</script>\n'
        f'<script>(function(){{'
        f'if (window.initAssetChart) {{'
        f'  var el = document.getElementById("chartdata-{safe_id}");'
        f'  try {{ window.initAssetChart("{safe_id}", JSON.parse(el.textContent)); }}'
        f'  catch (e) {{ console.error("chart-{safe_id} init failed", e); }}'
        f'}}'
        f'}})();</script>'
    )


def _v2_momentum_meta(trades: List[dict]) -> dict:
    """Momentum bot metadata for the Momentum tab (J.1 minimal; J.3 expands)."""
    try:
        from config import ASSETS
    except ImportError:
        ASSETS = {}

    asset_rows = [
        {"name": k, "symbol": v.get("symbol", ""),
         "interval": v.get("interval", "")}
        for k, v in ASSETS.items()
    ]
    momentum_trades = [t for t in trades if t.get("bot") == "Momentum"]
    closed = [t for t in momentum_trades
              if t.get("exit_price") not in (None, 0, "0", "")]
    pnl_list = [float(t.get("net_pnl") or 0) for t in closed]
    return {
        "paused":              False,
        "state_label":         "LIVE",
        "assets":              asset_rows,
        "closed_count":        len(closed),
        "win_rate_display":    (f"{sum(1 for p in pnl_list if p > 0) / len(pnl_list) * 100:.1f}%"
                                if pnl_list else "—"),
        "net_pnl_display":     _v2_pnl_display(sum(pnl_list)),
    }


def _v2_breakout_meta(trades: List[dict]) -> dict:
    """Breakout-specific metadata for the Breakout tab."""
    try:
        from breakout_config import (
            BREAKOUT_PAUSED, BREAKOUT_ASSETS,
            BREAKOUT_MARGIN_PER_TRADE, BREAKOUT_LEVERAGE,
            MAX_BREAKOUT_POSITIONS,
        )
    except ImportError:
        BREAKOUT_PAUSED = True
        BREAKOUT_ASSETS = {}
        BREAKOUT_MARGIN_PER_TRADE = 25.0
        BREAKOUT_LEVERAGE = 10
        MAX_BREAKOUT_POSITIONS = 2

    first_cfg = next(iter(BREAKOUT_ASSETS.values()), {})
    asset_rows = [
        {"name": k, "symbol": v.get("symbol", ""),
         "interval": v.get("interval", ""),
         "allow_short": bool(v.get("allow_short", False))}
        for k, v in BREAKOUT_ASSETS.items()
    ]
    bo_trades = [t for t in trades if t.get("bot") == "Breakout"]
    closed = [t for t in bo_trades
              if t.get("exit_price") not in (None, 0, "0", "")]
    pnl_list = [float(t.get("net_pnl") or 0) for t in closed]
    return {
        "paused":                bool(BREAKOUT_PAUSED),
        "donchian_period":       first_cfg.get("donchian_period", 20),
        "donchian_exit_period":  first_cfg.get("donchian_exit_period", 10),
        "adx_threshold":         first_cfg.get("adx_threshold", 20),
        "adx_exit_threshold":    first_cfg.get("adx_exit_threshold", 15),
        "sl_atr_mult":           first_cfg.get("sl_atr_mult", 1.5),
        "margin_usd":            float(BREAKOUT_MARGIN_PER_TRADE),
        "leverage":              int(BREAKOUT_LEVERAGE),
        "notional_usd":          float(BREAKOUT_MARGIN_PER_TRADE) * int(BREAKOUT_LEVERAGE),
        "max_positions":         int(MAX_BREAKOUT_POSITIONS),
        "assets":                asset_rows,
        "closed_count":          len(closed),
        "win_rate_display":      (f"{sum(1 for p in pnl_list if p > 0) / len(pnl_list) * 100:.1f}%"
                                  if pnl_list else "—"),
        "net_pnl_display":       _v2_pnl_display(sum(pnl_list)),
    }


def _v2_pair_meta(trades: List[dict], state: dict | None = None) -> dict:
    """Pair-specific metadata for the Pair tab."""
    try:
        from pair_config import (
            PAIR_PAUSED, PAIR_CONFIG, PAIR_INTERVAL,
            PAIR_LONG_SYMBOL, PAIR_SHORT_SYMBOL,
            PAIR_LONG_LEG_KEY, PAIR_MARGIN_PER_LEG, PAIR_LEVERAGE,
        )
    except ImportError:
        PAIR_PAUSED = True
        PAIR_CONFIG = {"z_window": 30, "entry_z": 2.0, "exit_z": 0.5,
                       "max_hold_bars": 5, "atr_stop_mult": 2.0}
        PAIR_INTERVAL = "1d"
        PAIR_LONG_SYMBOL = "ETHUSDT"
        PAIR_SHORT_SYMBOL = "BTCUSDT"
        PAIR_LONG_LEG_KEY = "PAIR_ETHBTC_LONG_LEG"
        PAIR_MARGIN_PER_LEG = 50.0
        PAIR_LEVERAGE = 10

    open_pos = None
    if state:
        long_leg = state.get("positions", {}).get(PAIR_LONG_LEG_KEY)
        if long_leg:
            open_pos = {
                "direction":   long_leg.get("direction", "—"),
                "entry_ratio": float(long_leg.get("entry_ratio") or 0),
                "entry_z":     float(long_leg.get("entry_z") or 0),
                "bars_held":   int(long_leg.get("bars_held") or 0),
            }

    pair_trades = [t for t in trades if t.get("bot") == "Pair"]
    closed = [t for t in pair_trades
              if t.get("exit_price") not in (None, 0, "0", "")]
    pnl_list = [float(t.get("net_pnl") or 0) for t in closed]
    return {
        "paused":            bool(PAIR_PAUSED),
        "long_symbol":       PAIR_LONG_SYMBOL,
        "short_symbol":      PAIR_SHORT_SYMBOL,
        "interval":          PAIR_INTERVAL,
        "z_window":          int(PAIR_CONFIG.get("z_window", 30)),
        "entry_z":           float(PAIR_CONFIG.get("entry_z", 2.0)),
        "exit_z":            float(PAIR_CONFIG.get("exit_z", 0.5)),
        "max_hold_bars":     int(PAIR_CONFIG.get("max_hold_bars", 5)),
        "atr_stop_mult":     float(PAIR_CONFIG.get("atr_stop_mult", 2.0)),
        "margin_usd":        float(PAIR_MARGIN_PER_LEG),
        "leverage":          int(PAIR_LEVERAGE),
        "notional_usd":      float(PAIR_MARGIN_PER_LEG) * int(PAIR_LEVERAGE),
        "open_position":     open_pos,
        "closed_count":      len(closed) // 2,  # each pair = 2 journal rows
        "win_rate_display":  (f"{sum(1 for p in pnl_list if p > 0) / len(pnl_list) * 100:.1f}%"
                              if pnl_list else "—"),
        "net_pnl_display":   _v2_pnl_display(sum(pnl_list)),
    }


def _v2_reversal_meta(trades: List[dict]) -> dict:
    """Reversal-specific metadata for the Reversal tab."""
    try:
        from reversal_config import (
            REVERSAL_PAUSED, REVERSAL_ASSETS,
            REVERSAL_MARGIN_PER_TRADE, REVERSAL_LEVERAGE,
        )
    except ImportError:
        REVERSAL_PAUSED = True
        REVERSAL_ASSETS = {}
        REVERSAL_MARGIN_PER_TRADE = 25.0
        REVERSAL_LEVERAGE = 10

    first_cfg = next(iter(REVERSAL_ASSETS.values()), {})
    asset_rows = [
        {"name": k, "symbol": v.get("symbol", ""),
         "interval": v.get("interval", ""),
         "allow_long":  bool(v.get("allow_long", True)),
         "allow_short": bool(v.get("allow_short", True))}
        for k, v in REVERSAL_ASSETS.items()
    ]
    rev_trades = [t for t in trades if t.get("bot") == "Reversal"]
    closed = [t for t in rev_trades
              if t.get("exit_price") not in (None, 0, "0", "")]
    pnl_list = [float(t.get("net_pnl") or 0) for t in closed]
    return {
        "paused":              bool(REVERSAL_PAUSED),
        "rsi_length":          first_cfg.get("rsi_length", 15),
        "oversold":            first_cfg.get("oversold", 10.0),
        "overbought":          first_cfg.get("overbought", 90.0),
        "range_mult":          first_cfg.get("range_mult", 3.0),
        "range_sma_length":    first_cfg.get("range_sma_length", 14),
        "close_position_pct":  first_cfg.get("close_position_pct", 0.30),
        "sl_atr_mult":         first_cfg.get("sl_atr_mult", 1.5),
        "max_hold_bars":       first_cfg.get("max_hold_bars", 24),
        "margin_usd":          float(REVERSAL_MARGIN_PER_TRADE),
        "leverage":            int(REVERSAL_LEVERAGE),
        "notional_usd":        float(REVERSAL_MARGIN_PER_TRADE) * int(REVERSAL_LEVERAGE),
        "assets":              asset_rows,
        "closed_count":        len(closed),
        "win_rate_display":    (f"{sum(1 for p in pnl_list if p > 0) / len(pnl_list) * 100:.1f}%"
                                if pnl_list else "—"),
        "net_pnl_display":     _v2_pnl_display(sum(pnl_list)),
    }


_PAUSE_FLAGS = {
    "whale":    ("whale_config",    "WHALE_PAUSED"),
    "breakout": ("breakout_config", "BREAKOUT_PAUSED"),
    "pair":     ("pair_config",     "PAIR_PAUSED"),
    "reversal": ("reversal_config", "REVERSAL_PAUSED"),
}


def _is_paused(bot_class: str) -> bool:
    """Read the bot's pause flag from its config module; default False if missing."""
    spec = _PAUSE_FLAGS.get(bot_class)
    if not spec:
        return False
    mod_name, flag_name = spec
    try:
        mod = __import__(mod_name)
        return bool(getattr(mod, flag_name, False))
    except ImportError:
        return False


def _v2_state(bot_class: str, status: dict) -> str:
    """Map heartbeat freshness + pause flags to the four V2 state names.

    Whale / breakout / pair are treated as "dormant" whenever their
    pause flag is set — the paused state is the operational reality
    regardless of whether the daemon is still writing heartbeats.
    """
    if _is_paused(bot_class):
        return "dormant"
    css = (status or {}).get("css", "")
    if css == "live":
        return "live"
    if css == "never":
        return "never"
    return "silent"


def _v2_seen_label(bot_class: str, status: dict) -> str:
    if _is_paused(bot_class):
        return "paused"
    return (status or {}).get("age", "—")


def _v2_pnl_display(amount: float) -> str:
    """Sign-aware PnL string. Empty input → '$0.00'."""
    if amount > 0:
        return f"+${amount:,.2f}"
    if amount < 0:
        return f"−${abs(amount):,.2f}"
    return "$0.00"


def _v2_trade_rows(trades: List[dict]) -> List[dict]:
    """Shape journal trades for the V2 trade log template.

    Sorted newest-first by date_opened. Open positions (exit_price None)
    render with em-dashes and result="OPEN". Each row carries the css
    helper classes the table needs so the template stays presentational.
    """
    def _result_class(r: str) -> str:
        return {"WIN": "is-up", "LOSS": "is-down",
                "FLAT": "is-flat", "OPEN": "is-open"}.get(r, "")

    def _pnl_cell(r: dict) -> str:
        pnl = float(r.get("net_pnl") or 0)
        if r.get("result") == "OPEN":
            return "—"
        return _v2_pnl_display(pnl)

    def _exit_cell(r: dict) -> str:
        ep = r.get("exit_price")
        if ep in (None, 0, "0", ""):
            return "—"
        try:
            f = float(ep)
        except (TypeError, ValueError):
            return "—"
        if f == 0:
            return "—"
        return f"{f:g}"

    def _short_date(s: str) -> str:
        # "2026-05-15T08:02:00..." → "2026-05-15 08:02"
        if not s:
            return ""
        s = str(s).replace("T", " ")
        return s[:16]

    rows = sorted(
        trades,
        key=lambda r: r.get("date_opened") or "",
        reverse=True,
    )
    out: List[dict] = []
    for i, r in enumerate(rows, start=1):
        result = r.get("result") or "OPEN"
        out.append({
            "row_num":        len(rows) - i + 1,   # newest = highest #
            "id":             r.get("id"),
            "date_opened":    _short_date(r.get("date_opened")),
            "symbol":         r.get("symbol", ""),
            "direction":      r.get("direction", ""),
            "bot":            r.get("bot", ""),
            "bot_class":      (r.get("bot") or "").lower() or "momentum",
            "strategy":       r.get("strategy", ""),
            "entry_price":    f"{float(r.get('entry_price') or 0):g}",
            "exit_price":     _exit_cell(r),
            "quantity":       f"{float(r.get('quantity') or 0):g}",
            "leverage":       int(r.get("leverage") or 1),
            "net_pnl":        float(r.get("net_pnl") or 0),
            "net_pnl_display": _pnl_cell(r),
            "exit_reason":    r.get("exit_reason") or "",
            "result":         result,
            "result_class":   _result_class(result),
        })
    return out


def build_dashboard(executor, state: dict) -> None:
    """Gather live data and render the dashboard via Jinja2 templates."""
    from dashboard_renderer import render

    data = gather_dashboard_data(executor, state)
    ctx = _build_v2_context(data, state=state, executor=executor)
    html = render("base.html.j2", ctx)
    DASHBOARD_FILE.write_text(html, encoding="utf-8")
    logger.info("Dashboard written to %s", DASHBOARD_FILE)
