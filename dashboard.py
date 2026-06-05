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
    """Compute performance metrics from trade history."""
    if not trades:
        return {
            "win_rate": 0, "profit_factor": 0, "avg_win": 0, "avg_loss": 0,
            "best_trade": 0, "worst_trade": 0, "max_drawdown": 0,
            "sharpe": 0, "expectancy": 0, "total_trades": 0,
        }

    # Win rate denominator must be CLOSED trades only. Including open paper
    # positions in the total made WR look ~37% when the real value was ~56%
    # (peer-review feedback, May 2026).
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

    # Max drawdown from equity curve (closed trades only)
    equity = INITIAL_CAPITAL
    peak = equity
    max_dd = 0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

    # Sharpe approximation (annualized, assuming 6 trades/month)
    import statistics
    if len(pnls) > 1:
        mean_pnl = statistics.mean(pnls)
        std_pnl = statistics.stdev(pnls)
        sharpe = (mean_pnl / std_pnl * (72 ** 0.5)) if std_pnl > 0 else 0
    else:
        sharpe = 0

    expectancy = statistics.mean(pnls) if pnls else 0

    return {
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "best_trade": round(best, 2),
        "worst_trade": round(worst, 2),
        "max_drawdown": round(max_dd, 1),
        "sharpe": round(sharpe, 2),
        "expectancy": round(expectancy, 2),
        "total_trades": total_closed,        # closed-only count, used in WR display
        "open_positions": open_count,        # separate exposure metric
        "all_trades_count": len(trades),     # raw row count for sanity
    }


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

    return {"momentum": momentum, "whale": whale, "funding": funding}


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


def _build_v2_context(data: Dict[str, Any]) -> Dict[str, Any]:
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
        "whale_meta":   _v2_whale_meta(trades),
        "funding_meta": _v2_funding_meta(trades),
        "projection":   _v2_projection(),
    }


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
        ],
        "portfolio": {"net_pnl": 0, "net_pnl_display": "$0.00",
                      "closed_count": 0, "open_count": 0,
                      "win_rate_display": "—",
                      "spark_svg": _v2_sparkline_svg(
                          _v2_sparkline_points(trades), width=200, height=32,
                          stroke_class="spark__line",
                          label="Portfolio 30-day cumulative PnL")},
        "trades":       _v2_trade_rows(trades),
        "whale_meta":   _v2_whale_meta(trades),
        "funding_meta": _v2_funding_meta(trades),
        "projection":   _v2_projection(),
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
        s = t.get("date_closed") or t.get("date_opened") or ""
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


def _v2_state(bot_class: str, status: dict) -> str:
    """Map heartbeat freshness + pause flags to the four V2 state names.

    Whale is treated as "dormant" whenever WHALE_PAUSED is true regardless
    of heartbeat — the paused state is the operational reality.
    """
    if bot_class == "whale":
        try:
            from whale_config import WHALE_PAUSED
            if WHALE_PAUSED:
                return "dormant"
        except ImportError:
            pass
    css = (status or {}).get("css", "")
    if css == "live":
        return "live"
    if css == "never":
        return "never"
    return "silent"


def _v2_seen_label(bot_class: str, status: dict) -> str:
    if bot_class == "whale":
        try:
            from whale_config import WHALE_PAUSED
            if WHALE_PAUSED:
                return "paused"
        except ImportError:
            pass
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
    ctx = _build_v2_context(data)
    html = render("base.html.j2", ctx)
    DASHBOARD_FILE.write_text(html, encoding="utf-8")
    logger.info("Dashboard written to %s", DASHBOARD_FILE)
