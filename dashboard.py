"""Dashboard generator — produces a dark-themed HTML dashboard.

Pulls data from WEEX API and the Trading Journal, then writes
a self-contained dashboard.html with Chart.js charts.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

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
)

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

    wins = [t for t in trades if (t.get("net_pnl") or 0) > 0]
    losses = [t for t in trades if (t.get("net_pnl") or 0) < 0]
    pnls = [float(t.get("net_pnl") or 0) for t in trades]

    total = len(trades)
    win_count = len(wins)
    win_rate = (win_count / total * 100) if total > 0 else 0

    gross_profit = sum(float(t.get("net_pnl") or 0) for t in wins)
    gross_loss = abs(sum(float(t.get("net_pnl") or 0) for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 999

    avg_win = (gross_profit / win_count) if win_count > 0 else 0
    avg_loss = (gross_loss / len(losses)) if losses else 0

    best = max(pnls) if pnls else 0
    worst = min(pnls) if pnls else 0

    # Max drawdown from equity curve
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
        "total_trades": total,
    }


def _compute_bot_status() -> Dict[str, dict]:
    """Read heartbeat mtimes for both bots and classify as LIVE / STALE / NEVER.

    Momentum bot saves state.json every cycle (~5 min). Whale bot writes
    .whale_heartbeat every cycle (~15 min). Stale threshold = 2x poll interval.
    """
    from datetime import datetime, timezone
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

    return {"momentum": momentum, "whale": whale}


def gather_dashboard_data(executor, state: dict) -> Dict[str, Any]:
    """Gather all data needed for the dashboard."""
    data: Dict[str, Any] = {}

    # Account balance
    bal = executor.get_account_balance()
    data["balance"] = bal
    data["equity"] = float(bal.get("balance", 0) or 0)
    data["available"] = float(bal.get("availableBalance", 0) or 0)
    data["unrealized_pnl"] = float(bal.get("unrealizePnl", 0) or 0)

    # Open positions
    positions = executor.get_all_positions()
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

    # Daily PnL (last 30 days)
    daily_pnl: Dict[str, float] = {}
    for t in trades:
        date_str = str(t.get("date_closed", ""))[:10]
        if date_str:
            daily_pnl[date_str] = daily_pnl.get(date_str, 0) + float(t.get("net_pnl") or 0)
    sorted_days = sorted(daily_pnl.items())[-30:]
    data["daily_pnl_labels"] = [d[0] for d in sorted_days]
    data["daily_pnl_values"] = [round(d[1], 2) for d in sorted_days]

    # Equity curve
    equity_curve = [INITIAL_CAPITAL]
    for t in trades:
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

    # Show timestamp in Central Time (auto-handles CST/CDT)
    data["timestamp"] = datetime.now(CENTRAL_TZ).strftime("%Y-%m-%d %H:%M %Z")
    return data


def _render_signal_status_rows(signal_status: dict) -> str:
    """Render the Entry Signal Diagnostics rows.

    Iterates over the authoritative ASSETS config so every monitored asset gets
    a row, even if its signal_status entry is missing or stale. This keeps the
    diagnostics table complete during partial bot-1 outages (e.g. DRY_RUN
    without WEEX creds where only some assets fetch cleanly).
    """
    # Column order matters — keep consistent with header
    FILTER_COLUMNS = [
        ("trend", "Trend"),
        ("close_above_ema", "Price > EMA"),
        ("atr_regime", "ATR Regime"),
        ("rsi_crossover", "RSI Cross"),
        ("macd", "MACD"),
        ("pmo", "PMO"),
        ("volume", "Volume"),
        ("mfi", "MFI"),
        ("adx", "ADX"),
        ("btc_filter", "BTC Filter"),
    ]

    def cell(v):
        if v is True:
            return '<td style="text-align:center;color:#4ade80;font-size:1.2em;">✓</td>'
        if v is False:
            return '<td style="text-align:center;color:#f87171;font-size:1.2em;">✗</td>'
        return '<td style="text-align:center;opacity:0.35;">—</td>'

    # Sort asset keys to group by symbol for readability
    ordered_keys = sorted(ASSETS.keys(), key=lambda k: (ASSETS[k].get("symbol", ""), k))

    rows = ""
    for asset_name in ordered_keys:
        cfg = ASSETS.get(asset_name, {})
        info = signal_status.get(asset_name) or {}
        interval = info.get("interval") or cfg.get("interval", "")

        # Clean display label — strip _4H / _1D suffix since interval is shown separately
        display_label = asset_name
        for suffix in ("_4H", "_1D", "_1d", "_4h"):
            if display_label.endswith(suffix):
                display_label = display_label[: -len(suffix)]
                break

        if info:
            status_color = "#4ade80" if info.get("would_enter") else "#9ca3af"
            status_text = "READY 🟢" if info.get("would_enter") else "WAITING"
            blocked = info.get("blocked_by") or ""
            filters = info.get("filters", {})
            values = info.get("values", {})
            cells = "".join(cell(filters.get(k)) for k, _ in FILTER_COLUMNS)
            val_snippet = []
            if values.get("rsi") is not None:
                val_snippet.append(f"RSI {values['rsi']:.1f}")
            if values.get("mfi") is not None:
                val_snippet.append(f"MFI {values['mfi']:.1f}")
            if values.get("adx") is not None:
                val_snippet.append(f"ADX {values['adx']:.1f}")
            if values.get("btc_close") is not None and values.get("btc_ema") is not None:
                btc_state = "↑" if values["btc_close"] > values["btc_ema"] else "↓"
                val_snippet.append(f"BTC {btc_state}")
            val_str = " · ".join(val_snippet) if val_snippet else "—"
        else:
            status_color = "#64748b"
            status_text = "NO DATA"
            blocked = "awaiting next cycle"
            cells = "".join(cell(None) for _ in FILTER_COLUMNS)
            val_str = "—"

        rows += f"""<tr>
            <td><strong>{display_label}</strong> <span style="opacity:0.5;font-size:0.85em;">{interval}</span></td>
            <td style="color:{status_color};font-weight:600;">{status_text}</td>
            <td style="opacity:0.7;font-size:0.85em;">{blocked}</td>
            {cells}
            <td style="font-size:0.8em;opacity:0.7;">{val_str}</td>
        </tr>"""
    return rows


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


def _render_yearly_projection_rows(proj: dict) -> str:
    rows = proj["rows"]
    if not rows:
        return '<tr><td colspan="7" class="empty">No strategies with backtest stats configured</td></tr>'

    html = ""
    for r in rows:
        pnl_class = "green" if r["annual_pnl_live"] >= 0 else "red"
        display_name = r["name"]
        # Highlight the whale row visually so it stands out as a different bot
        row_style = ' style="background:#0d2227;"' if r.get("is_whale") else ''
        name_html = f"<strong>{display_name}</strong>"
        if r.get("is_whale"):
            note = r.get("source_note", "")
            name_html += f'<br><span style="font-size:0.78em;opacity:0.6;">{note}</span>'
        html += f"""<tr{row_style}>
            <td>{name_html}</td>
            <td>{r['symbol']}</td>
            <td>{r['interval']}</td>
            <td>{r['pf']:.2f}</td>
            <td>{r['trades_per_year']:.1f}</td>
            <td>{r['annual_pct_backtest']:+.2f}%</td>
            <td class="{pnl_class}"><strong>${r['annual_pnl_live']:+,.2f}</strong></td>
            <td class="red">{r['dd_pct']:.2f}%</td>
        </tr>"""
    return html


def _render_whale_signals_rows(signals: list) -> str:
    """Render the Whale Bot signals table rows (with Tier 1 confluence)."""
    if not signals:
        return '<tr><td colspan="13" style="text-align:center;opacity:0.6;">No whale signals recorded yet — waiting for first poll.</td></tr>'

    rows = ""
    for s in signals[:25]:
        sig_type = s.get("signal", "")
        direction = s.get("direction", "")
        dir_class = "badge-green" if direction == "LONG" else "badge-red"
        sig_class = "badge-yellow" if sig_type.startswith("DIVERGENCE") else "badge-blue"
        conf = s.get("confidence", 0)
        conf_color = "#4ade80" if conf >= 7 else ("#facc15" if conf >= 5 else "#f87171")

        # Tier 1 confluence fields (may be absent on pre-enrichment signals)
        funding_pct = s.get("funding_annual_pct")
        funding_cell = "—"
        if funding_pct is not None:
            funding_color = "#4ade80" if (direction == "LONG" and funding_pct < 0) or (direction == "SHORT" and funding_pct > 0) else ("#f87171" if abs(funding_pct) > 20 else "#a0a0a8")
            funding_cell = f'<span style="color:{funding_color}">{funding_pct:+.1f}%</span>'

        liq_adverse = s.get("liq_adverse_usd", 0) or 0
        liq_fuel = s.get("liq_fuel_usd", 0) or 0
        liq_cell = "—"
        if liq_adverse > 0 or liq_fuel > 0:
            liq_cell = (
                f'<span style="color:#f87171">-${liq_adverse/1e6:.1f}M</span> '
                f'<span style="color:#4ade80">+${liq_fuel/1e6:.1f}M</span>'
            )

        new_c = s.get("recency_new_count", 0) or 0
        grow_c = s.get("recency_growth_count", 0) or 0
        exit_c = s.get("recency_exit_count", 0) or 0
        recency_bits = []
        if new_c: recency_bits.append(f'<span style="color:#4ade80">+{new_c} new</span>')
        if grow_c: recency_bits.append(f'<span style="color:#4ade80">↑{grow_c}</span>')
        if exit_c: recency_bits.append(f'<span style="color:#f87171">-{exit_c} out</span>')
        recency_cell = " ".join(recency_bits) if recency_bits else "—"

        rows += f"""<tr>
            <td><b>{s.get('coin', '?')}</b></td>
            <td style="opacity:0.7;font-size:0.85em;">{s.get('weex_symbol', '')}</td>
            <td><span class="badge {sig_class}">{sig_type}</span></td>
            <td><span class="badge {dir_class}">{direction}</span></td>
            <td style="text-align:right;color:{conf_color};font-weight:700;">{conf}/10</td>
            <td style="text-align:right;">{s.get('smart_long_pct', 0):.0f}%</td>
            <td style="text-align:right;">{s.get('smart_short_pct', 0):.0f}%</td>
            <td style="text-align:right;">{s.get('smart_n', 0)}</td>
            <td style="text-align:right;font-size:0.9em;">{funding_cell}</td>
            <td style="text-align:right;font-size:0.85em;">{liq_cell}</td>
            <td style="text-align:right;font-size:0.85em;">{recency_cell}</td>
            <td style="opacity:0.8;font-size:0.85em;">{s.get('reasoning', '')}</td>
        </tr>"""
    return rows


def _render_whale_positions_rows(positions: list) -> str:
    """Render the open whale positions table rows."""
    if not positions:
        return '<tr><td colspan="9" style="text-align:center;opacity:0.6;">No open whale positions.</td></tr>'

    rows = ""
    for p in positions:
        direction = p.get("direction", "LONG")
        dir_class = "badge-green" if direction == "LONG" else "badge-red"
        entry = p.get("entry_price", 0)
        sl = p.get("sl") or 0
        tp = p.get("tp") or 0
        sig_type = p.get("signal_type", "")
        sig_class = "badge-yellow" if sig_type.startswith("DIVERGENCE") else "badge-blue"
        rows += f"""<tr>
            <td><b>{p.get('coin', '?')}</b></td>
            <td style="opacity:0.7;font-size:0.85em;">{p.get('symbol', '')}</td>
            <td><span class="badge {dir_class}">{direction}</span></td>
            <td style="text-align:right;">${entry:,.4f}</td>
            <td style="text-align:right;">{p.get('quantity', 0)}</td>
            <td style="text-align:right;color:#f87171;">${sl:,.4f}</td>
            <td style="text-align:right;color:#4ade80;">${tp:,.4f}</td>
            <td><span class="badge {sig_class}">{sig_type}</span></td>
            <td style="text-align:right;">${p.get('margin_usd', 0):.0f}</td>
        </tr>"""
    return rows


def generate_dashboard(data: dict, output_path: str = None) -> None:
    """Generate the HTML dashboard file with two tabs: Dashboard + Trade Log."""
    path = output_path or str(DASHBOARD_FILE)

    # Prepare chart data
    daily_labels = json.dumps(data.get("daily_pnl_labels", []))
    daily_values = json.dumps(data.get("daily_pnl_values", []))
    equity_curve = json.dumps(data.get("equity_curve", [INITIAL_CAPITAL]))
    alloc_labels = json.dumps(list(data.get("allocation", {}).keys()) + ["Available"])
    alloc_values = json.dumps(list(data.get("allocation", {}).values()) + [data.get("available", 0)])

    # v2: Signal Status rows for the Entry Signal Diagnostics panel
    signal_rows = _render_signal_status_rows(data.get("signal_status", {}))

    # Build positions rows
    pos_rows = ""
    for p in data.get("positions", []):
        sym = p.get("symbol", "?")
        amt = float(p.get("positionAmt", 0))
        side = "LONG" if amt > 0 else "SHORT"
        side_class = "badge-green" if side == "LONG" else "badge-red"
        entry = p.get("entryPrice", "?")
        mark = p.get("markPrice", "?")
        lev = p.get("leverage", "?")
        upnl = float(p.get("unrealizedProfit", 0) or 0)
        pnl_class = "green" if upnl >= 0 else "red"
        margin = float(p.get("positionInitialMargin", 0) or 0)
        roe = (upnl / margin * 100) if margin > 0 else 0
        liq = p.get("liquidationPrice", "N/A")
        pos_rows += f"""<tr>
            <td>{sym}</td>
            <td><span class="{side_class}">{side}</span></td>
            <td>{abs(amt)}</td>
            <td>{entry}</td>
            <td>{mark}</td>
            <td>{lev}x</td>
            <td class="{pnl_class}">${upnl:+.2f}</td>
            <td class="{pnl_class}">{roe:+.1f}%</td>
            <td>{liq}</td>
        </tr>"""

    if not pos_rows:
        pos_rows = '<tr><td colspan="9" class="empty">No open positions</td></tr>'

    # Build recent trade rows (dashboard tab — last 20)
    trade_rows = ""
    for t in reversed(data.get("recent_trades", [])):
        pnl = float(t.get("net_pnl") or 0)
        pnl_class = "green" if pnl > 0 else ("red" if pnl < 0 else "")
        exit_r = t.get("exit_reason", "")
        action = exit_r if exit_r else "OPEN"
        action_class = "badge-magenta"
        if "TP" in action:
            action_class = "badge-green"
        elif "SL" in action:
            action_class = "badge-red"
        elif "Stale" in action:
            action_class = "badge-gold"
        trade_rows += f"""<tr>
            <td>{t.get('date_closed', '')[:16]}</td>
            <td><span class="{action_class}">{action}</span></td>
            <td>{t.get('symbol', '')}</td>
            <td>{t.get('strategy', '')}</td>
            <td class="{pnl_class}">${pnl:+.2f}</td>
        </tr>"""

    if not trade_rows:
        trade_rows = '<tr><td colspan="5" class="empty">No trades yet</td></tr>'

    # Build FULL trade log rows (trade log tab — all trades, matching Excel columns)
    all_trades = data.get("trades", [])
    full_log_rows = ""
    for i, t in enumerate(reversed(all_trades), 1):
        pnl = float(t.get("net_pnl") or 0)
        gross = float(t.get("gross_pnl") or 0)
        fees = float(t.get("fees") or 0)
        entry_p = t.get("entry_price", 0)
        exit_p = t.get("exit_price", 0)
        qty = t.get("quantity", 0)
        lev = t.get("leverage", 1)
        margin = (float(entry_p) * float(qty) / int(lev)) if float(entry_p) and float(qty) and int(lev) else 0
        pnl_pct = (pnl / margin * 100) if margin > 0 else 0
        pnl_class = "green" if pnl > 0 else ("red" if pnl < 0 else "")
        result = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "FLAT")
        result_class = "badge-green" if pnl > 0 else ("badge-red" if pnl < 0 else "badge-gold")
        exit_r = t.get("exit_reason", "")
        exit_badge = "badge-green" if "TP" in exit_r else ("badge-red" if "SL" in exit_r else ("badge-gold" if "Stale" in exit_r else "badge-magenta"))
        full_log_rows += f"""<tr>
            <td>{len(all_trades) - i + 1}</td>
            <td>{t.get('date_opened', '')[:16]}</td>
            <td>{t.get('date_closed', '')[:16]}</td>
            <td>{t.get('symbol', '')}</td>
            <td>{t.get('direction', '')}</td>
            <td>{entry_p}</td>
            <td>{exit_p}</td>
            <td>{qty}</td>
            <td>{lev}x</td>
            <td>${gross:+.2f}</td>
            <td>${fees:.2f}</td>
            <td class="{pnl_class}">${pnl:+.2f}</td>
            <td class="{pnl_class}">{pnl_pct:+.1f}%</td>
            <td>{t.get('strategy', '')}</td>
            <td><span class="{exit_badge}">{exit_r}</span></td>
            <td><span class="{result_class}">{result}</span></td>
        </tr>"""

    if not full_log_rows:
        full_log_rows = '<tr><td colspan="16" class="empty">No closed trades yet</td></tr>'

    # Build funding rates rows
    funding_rows = ""
    for r in data.get("funding_rates", []):
        sym = r.get("symbol", "?")
        rate = float(r.get("lastFundingRate", 0))
        ann = r.get("annualized", 0)
        direction = r.get("direction", "?")
        dir_class = "badge-red" if direction == "SHORT" else "badge-green"
        funding_rows += f"""<tr>
            <td>{sym}</td>
            <td>{rate:+.6f}</td>
            <td>{ann:+.1f}%</td>
            <td><span class="{dir_class}">{direction}</span></td>
        </tr>"""

    if not funding_rows:
        funding_rows = '<tr><td colspan="4" class="empty">No funding data</td></tr>'

    # Metrics
    m = data.get("metrics", {})
    upnl = data.get("unrealized_pnl", 0)
    upnl_class = "green" if upnl >= 0 else "red"

    # Today's realized PnL (Central Time)
    today = datetime.now(CENTRAL_TZ).strftime("%Y-%m-%d")
    today_pnl = sum(float(t.get("net_pnl") or 0) for t in data.get("trades", [])
                     if str(t.get("date_closed", ""))[:10] == today)

    # Trade log data for export
    trades_json = json.dumps(all_trades)

    # Yearly projection
    projection = _compute_yearly_projection()
    projection_rows = _render_yearly_projection_rows(projection)
    proj_total_annual = projection["total_annual"]
    proj_total_class = "green" if proj_total_annual >= 0 else "red"
    proj_total_pct = (proj_total_annual / INITIAL_CAPITAL * 100) if INITIAL_CAPITAL > 0 else 0

    # Bot status pills (header)
    bot_status = data.get("bot_status") or {}
    momentum_status = bot_status.get("momentum") or {"text": "NEVER", "css": "never", "age": "never run"}
    whale_status = bot_status.get("whale") or {"text": "NEVER", "css": "never", "age": "never run"}
    momentum_status_text = momentum_status["text"]
    momentum_status_css = momentum_status["css"]
    momentum_status_age = momentum_status["age"]
    whale_status_text = whale_status["text"]
    whale_status_css = whale_status["css"]
    whale_status_age = whale_status["age"]

    # Whale bot rendering
    whale_signals_rows = _render_whale_signals_rows(data.get("whale_signals_latest", []))
    whale_positions_rows = _render_whale_positions_rows(data.get("whale_positions", []))
    whale_metrics = data.get("whale_metrics", {})
    whale_trade_count = whale_metrics.get("total_trades", 0)
    whale_win_rate = whale_metrics.get("win_rate", 0)
    whale_pf = whale_metrics.get("profit_factor", 0)
    whale_net_pnl = sum(float(t.get("net_pnl") or 0) for t in data.get("whale_trades", []))
    whale_net_class = "green" if whale_net_pnl >= 0 else "red"
    whale_signals_ts = data.get("whale_signals_ts", "never")
    whale_open_count = len(data.get("whale_positions", []))
    proj_trades_per_year = projection["total_trades_per_year"]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crypto Bot Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #060607; color: #f0f0f0; font-family: 'Segoe UI', system-ui, sans-serif; padding: 20px; }}
h1 {{ text-align: center; color: #00d4d4; margin-bottom: 5px; font-size: 1.8em; }}
.subtitle {{ text-align: center; color: #a0a0a8; margin-bottom: 20px; font-size: 0.85em; }}

/* ── Tabs ── */
.tab-bar {{ display: flex; gap: 0; margin-bottom: 25px; border-bottom: 2px solid #1c1c2e; }}
.tab-btn {{ padding: 12px 28px; background: none; border: none; color: #a0a0a8; font-size: 1em; font-weight: 600; cursor: pointer; border-bottom: 3px solid transparent; transition: all 0.2s; letter-spacing: 0.5px; }}
.tab-btn:hover {{ color: #f0f0f0; background: #0d0d14; }}
.tab-btn.active {{ color: #00d4d4; border-bottom-color: #00d4d4; }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}

/* ── Stats Bar ── */
.stats-bar {{ display: flex; gap: 15px; margin-bottom: 25px; flex-wrap: wrap; }}
.stat-card {{ flex: 1; min-width: 160px; background: #0d0d14; border-radius: 12px; padding: 18px; text-align: center; border: 1px solid #1c1c2e; }}
.stat-card .label {{ color: #a0a0a8; font-size: 0.8em; text-transform: uppercase; letter-spacing: 1px; }}
.stat-card .value {{ font-size: 1.6em; font-weight: 700; margin-top: 5px; }}

/* ── Colors ── */
.green {{ color: #00c853; }}
.red {{ color: #e5173f; }}
.teal {{ color: #00d4d4; }}

/* ── Sections ── */
.section {{ background: #0d0d14; border-radius: 12px; padding: 20px; margin-bottom: 20px; border: 1px solid #1c1c2e; }}
.section h2 {{ color: #00d4d4; font-size: 1.1em; margin-bottom: 15px; border-bottom: 1px solid #1c1c2e; padding-bottom: 8px; }}

/* ── Tables ── */
table {{ width: 100%; border-collapse: collapse; }}
th {{ color: #a0a0a8; font-size: 0.8em; text-transform: uppercase; text-align: left; padding: 8px 10px; border-bottom: 1px solid #1c1c2e; }}
td {{ padding: 8px 10px; border-bottom: 1px solid #0a0a0e; font-size: 0.9em; }}
tr:nth-child(even) {{ background: #0a0a0e; }}
.empty {{ text-align: center; color: #555; padding: 20px; }}

/* ── Badges ── */
.badge-green {{ background: #00c85320; color: #00c853; padding: 3px 10px; border-radius: 12px; font-size: 0.8em; font-weight: 600; }}
.badge-red {{ background: #e5173f20; color: #e5173f; padding: 3px 10px; border-radius: 12px; font-size: 0.8em; font-weight: 600; }}
.badge-magenta {{ background: #d040f020; color: #d040f0; padding: 3px 10px; border-radius: 12px; font-size: 0.8em; font-weight: 600; }}
.badge-gold {{ background: #c8c82020; color: #c8c820; padding: 3px 10px; border-radius: 12px; font-size: 0.8em; font-weight: 600; }}
.badge-blue {{ background: #1565c020; color: #5090e0; padding: 3px 10px; border-radius: 12px; font-size: 0.8em; font-weight: 600; }}

/* ── Grid Layouts ── */
.grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }}
.grid-3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
.metric-box {{ background: #0a0a0e; border-radius: 8px; padding: 15px; text-align: center; }}
.metric-box .m-label {{ color: #a0a0a8; font-size: 0.75em; text-transform: uppercase; }}
.metric-box .m-value {{ font-size: 1.3em; font-weight: 700; margin-top: 4px; }}
canvas {{ max-height: 300px; }}

/* ── Telegram Cards ── */
.tg-card {{ background: #0a0a0e; border-radius: 8px; padding: 12px 16px; margin-bottom: 10px; border-left: 3px solid #d040f0; }}
.tg-cmd {{ color: #d040f0; font-family: monospace; font-weight: 600; }}
.tg-resp {{ color: #ccc; font-size: 0.9em; margin-top: 5px; }}
.scroll-table {{ max-height: 400px; overflow-y: auto; }}

/* ── Trade Log Tab ── */
.log-toolbar {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; flex-wrap: wrap; gap: 10px; }}
.log-toolbar .log-stats {{ color: #a0a0a8; font-size: 0.9em; }}
.log-toolbar .log-stats span {{ color: #f0f0f0; font-weight: 600; }}
.btn-export {{ padding: 8px 20px; border-radius: 8px; border: 1px solid #00d4d4; background: #00d4d410; color: #00d4d4; font-weight: 600; cursor: pointer; font-size: 0.85em; transition: all 0.2s; }}
.btn-export:hover {{ background: #00d4d430; }}

/* ── Bot status pills (header) ── */
.bot-status-bar {{ display: inline-flex; gap: 10px; margin-left: 15px; vertical-align: middle; }}
.status-pill {{ display: inline-flex; align-items: center; padding: 3px 12px; border-radius: 14px; font-size: 0.78em; font-weight: 600; }}
.status-pill .dot {{ width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; display: inline-block; }}
.status-pill .age {{ opacity: 0.6; margin-left: 8px; font-weight: 400; font-size: 0.95em; }}
.status-pill.live    {{ background: #00c85322; color: #4ade80; }}
.status-pill.live .dot    {{ background: #4ade80; box-shadow: 0 0 8px #4ade80; animation: pulse 2s infinite; }}
.status-pill.stale   {{ background: #ffc10722; color: #facc15; }}
.status-pill.stale .dot   {{ background: #facc15; }}
.status-pill.never   {{ background: #94a3b822; color: #94a3b8; }}
.status-pill.never .dot   {{ background: #94a3b8; }}
@keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.5; }} }}

/* ── Export modal ── */
.modal-overlay {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.75); z-index: 1000; align-items: center; justify-content: center; padding: 20px; }}
.modal-overlay.show {{ display: flex; }}
.modal-content {{ background: #0d0d14; border: 1px solid #2a2a5e; border-radius: 14px; padding: 28px; max-width: 800px; width: 100%; max-height: 90vh; overflow-y: auto; }}
.modal-content h2 {{ margin: 0 0 20px 0; color: #00d4d4; }}
.modal-filters {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-bottom: 20px; }}
.modal-filters label {{ display: block; color: #a0a0a8; font-size: 0.78em; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }}
.modal-filters input, .modal-filters select {{ width: 100%; padding: 9px 12px; background: #0a0a0e; color: #e0e0e0; border: 1px solid #2a2a5e; border-radius: 8px; font-size: 0.92em; box-sizing: border-box; }}
.modal-filters input:focus, .modal-filters select:focus {{ border-color: #00d4d4; outline: none; }}
.modal-filters .date-range {{ display: flex; gap: 6px; align-items: center; }}
.modal-filters .date-range input {{ flex: 1; }}
.modal-preview-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; padding: 8px 0; border-top: 1px solid #1c1c2e; }}
.modal-preview-count {{ color: #a0a0a8; font-size: 0.9em; }}
.modal-preview-count b {{ color: #00d4d4; font-size: 1.1em; }}
.modal-preview {{ max-height: 280px; overflow-y: auto; border: 1px solid #1c1c2e; border-radius: 8px; }}
.modal-preview table {{ width: 100%; font-size: 0.82em; border-collapse: collapse; }}
.modal-preview th {{ position: sticky; top: 0; background: #0a0a0e; color: #a0a0a8; text-align: left; padding: 8px 10px; font-weight: 500; border-bottom: 1px solid #2a2a5e; }}
.modal-preview td {{ padding: 6px 10px; border-bottom: 1px solid #1c1c2e; }}
.modal-actions {{ display: flex; gap: 10px; justify-content: flex-end; margin-top: 20px; align-items: center; }}
.btn-primary {{ padding: 10px 24px; border-radius: 8px; border: none; background: #00d4d4; color: #0a0a0e; font-weight: 700; cursor: pointer; font-size: 0.9em; transition: all 0.2s; }}
.btn-primary:hover {{ background: #4dffff; }}
.btn-primary:disabled {{ background: #1c1c2e; color: #555; cursor: not-allowed; }}
.btn-secondary {{ padding: 10px 24px; border-radius: 8px; border: 1px solid #2a2a5e; background: transparent; color: #a0a0a8; font-weight: 600; cursor: pointer; font-size: 0.9em; }}
.btn-secondary:hover {{ background: #1c1c2e; color: #e0e0e0; }}

/* ── Bitcoin spinner ── */
.spinner-bitcoin {{ display: inline-block; font-size: 1.6em; color: #f7931a; animation: spin-btc 1s linear infinite; margin-right: auto; padding-left: 8px; }}
@keyframes spin-btc {{ from {{ transform: rotate(0deg); }} to {{ transform: rotate(360deg); }} }}
.full-log {{ overflow-x: auto; }}
.full-log table {{ min-width: 1200px; }}
.full-log th {{ position: sticky; top: 0; background: #0d0d14; z-index: 1; }}

@media (max-width: 768px) {{ .grid-2 {{ grid-template-columns: 1fr; }} .grid-3 {{ grid-template-columns: 1fr 1fr; }} }}
</style>
</head>
<body>

<h1>Crypto Trading Bot</h1>
<p class="subtitle">Last updated: {data.get('timestamp', 'N/A')}
    <span class="bot-status-bar">
        <span class="status-pill {momentum_status_css}" title="Momentum bot heartbeat">
            <span class="dot"></span>Momentum: {momentum_status_text}<span class="age">{momentum_status_age}</span>
        </span>
        <span class="status-pill {whale_status_css}" title="Whale bot heartbeat">
            <span class="dot"></span>Whale: {whale_status_text}<span class="age">{whale_status_age}</span>
        </span>
    </span>
</p>

<!-- Tab Navigation -->
<div class="tab-bar">
    <button class="tab-btn active" onclick="switchTab('dashboard')">Dashboard</button>
    <button class="tab-btn" onclick="switchTab('projection')">Yearly Projection</button>
    <button class="tab-btn" onclick="switchTab('whale')">Whale Bot</button>
    <button class="tab-btn" onclick="switchTab('tradelog')">Trade Log</button>
</div>

<!-- ═══════════════════ TAB 1: DASHBOARD ═══════════════════ -->
<div id="tab-dashboard" class="tab-content active">

<!-- Top Stats Bar -->
<div class="stats-bar">
    <div class="stat-card">
        <div class="label">Portfolio Value</div>
        <div class="value">${data.get('equity', 0):,.2f}</div>
    </div>
    <div class="stat-card">
        <div class="label">Unrealized PnL</div>
        <div class="value {upnl_class}">${upnl:+,.2f}</div>
    </div>
    <div class="stat-card">
        <div class="label">Today's PnL</div>
        <div class="value {'green' if today_pnl >= 0 else 'red'}">${today_pnl:+,.2f}</div>
    </div>
    <div class="stat-card">
        <div class="label">Win Rate</div>
        <div class="value">{m.get('win_rate', 0)}%</div>
    </div>
    <div class="stat-card">
        <div class="label">Total Trades</div>
        <div class="value">{m.get('total_trades', 0)}</div>
    </div>
</div>

<!-- Charts Row -->
<div class="grid-2">
    <div class="section">
        <h2>Daily PnL (30 Days)</h2>
        <canvas id="dailyPnlChart"></canvas>
    </div>
    <div class="section">
        <h2>Equity Curve</h2>
        <canvas id="equityChart"></canvas>
    </div>
</div>

<!-- Entry Signal Diagnostics (v2) -->
<div class="section">
    <h2>Entry Signal Diagnostics</h2>
    <p style="opacity:0.6;font-size:0.85em;margin-top:-8px;">
        Live per-filter breakdown. <span style="color:#4ade80;">✓</span> pass &nbsp;
        <span style="color:#f87171;">✗</span> blocked &nbsp;
        <span style="opacity:0.4;">—</span> not applicable. Recomputed each 5-min cycle.
    </p>
    <div class="scroll-table">
    <table style="font-size:0.9em;">
        <tr>
            <th>Asset</th>
            <th>Status</th>
            <th>Blocked By</th>
            <th>Trend</th>
            <th>Px&gt;EMA</th>
            <th>ATR</th>
            <th>RSI</th>
            <th>MACD</th>
            <th>PMO</th>
            <th>Vol</th>
            <th>MFI</th>
            <th>ADX</th>
            <th>BTC</th>
            <th>Values</th>
        </tr>
        {signal_rows}
    </table>
    </div>
</div>

<!-- Open Positions -->
<div class="section">
    <h2>Open Positions</h2>
    <div class="scroll-table">
    <table>
        <tr><th>Pair</th><th>Side</th><th>Size</th><th>Entry</th><th>Mark</th><th>Lev</th><th>uPnL</th><th>ROE%</th><th>Liq Price</th></tr>
        {pos_rows}
    </table>
    </div>
</div>

<!-- Trade Log + Metrics -->
<div class="grid-2">
    <div class="section">
        <h2>Recent Trades</h2>
        <div class="scroll-table">
        <table>
            <tr><th>Time</th><th>Action</th><th>Pair</th><th>Strategy</th><th>PnL</th></tr>
            {trade_rows}
        </table>
        </div>
    </div>
    <div class="section">
        <h2>Performance Metrics</h2>
        <div class="grid-3">
            <div class="metric-box"><div class="m-label">Win Rate</div><div class="m-value">{m.get('win_rate', 0)}%</div></div>
            <div class="metric-box"><div class="m-label">Profit Factor</div><div class="m-value">{m.get('profit_factor', 0)}</div></div>
            <div class="metric-box"><div class="m-label">Avg Win</div><div class="m-value green">${m.get('avg_win', 0)}</div></div>
            <div class="metric-box"><div class="m-label">Avg Loss</div><div class="m-value red">-${m.get('avg_loss', 0)}</div></div>
            <div class="metric-box"><div class="m-label">Best Trade</div><div class="m-value green">${m.get('best_trade', 0)}</div></div>
            <div class="metric-box"><div class="m-label">Worst Trade</div><div class="m-value red">${m.get('worst_trade', 0)}</div></div>
            <div class="metric-box"><div class="m-label">Max Drawdown</div><div class="m-value red">{m.get('max_drawdown', 0)}%</div></div>
            <div class="metric-box"><div class="m-label">Sharpe Ratio</div><div class="m-value">{m.get('sharpe', 0)}</div></div>
            <div class="metric-box"><div class="m-label">Expectancy</div><div class="m-value">${m.get('expectancy', 0)}</div></div>
        </div>
    </div>
</div>

<!-- Allocation + Funding -->
<div class="grid-2">
    <div class="section">
        <h2>Portfolio Allocation</h2>
        <canvas id="allocChart"></canvas>
    </div>
    <div class="section">
        <h2>Funding Rate Opportunities</h2>
        <div class="scroll-table">
        <table>
            <tr><th>Pair</th><th>Rate</th><th>Ann. APR</th><th>Direction</th></tr>
            {funding_rows}
        </table>
        </div>
    </div>
</div>

<!-- Telegram Preview -->
<div class="section">
    <h2>Telegram Commands</h2>
    <div class="tg-card">
        <div class="tg-cmd">/balance</div>
        <div class="tg-resp">Equity: ${data.get('equity', 0):,.2f} | Available: ${data.get('available', 0):,.2f} | uPnL: ${upnl:+.2f}</div>
    </div>
    <div class="tg-card">
        <div class="tg-cmd">/positions</div>
        <div class="tg-resp">{len(data.get('positions', []))} open positions across BTC, ETH, XRP</div>
    </div>
    <div class="tg-card">
        <div class="tg-cmd">/pnl</div>
        <div class="tg-resp">Today: ${today_pnl:+.2f} | 7d Win Rate: {m.get('win_rate', 0)}% | PF: {m.get('profit_factor', 0)}</div>
    </div>
    <div class="tg-card">
        <div class="tg-cmd">/signals</div>
        <div class="tg-resp">Monitoring BTC (4H), ETH (4H), XRP (Daily) for momentum entry signals...</div>
    </div>
</div>

</div><!-- end tab-dashboard -->

<!-- ═══════════════════ TAB 2: TRADE LOG ═══════════════════ -->
<div id="tab-tradelog" class="tab-content">

<div class="section">
    <div class="log-toolbar">
        <div class="log-stats">
            Total Trades: <span>{m.get('total_trades', 0)}</span> &nbsp;|&nbsp;
            Wins: <span class="green">{len([t for t in all_trades if float(t.get('net_pnl') or 0) > 0])}</span> &nbsp;|&nbsp;
            Losses: <span class="red">{len([t for t in all_trades if float(t.get('net_pnl') or 0) < 0])}</span> &nbsp;|&nbsp;
            Net PnL: <span class="{'green' if sum(float(t.get('net_pnl') or 0) for t in all_trades) >= 0 else 'red'}">${sum(float(t.get('net_pnl') or 0) for t in all_trades):+,.2f}</span>
        </div>
        <button class="btn-export" onclick="openExportModal()">Export Trades</button>
    </div>
    <h2>Closed Trades</h2>
    <div class="full-log scroll-table" style="max-height: 700px;">
    <table id="tradeLogTable">
        <thead>
        <tr>
            <th>#</th><th>Date Opened</th><th>Date Closed</th><th>Symbol</th><th>Direction</th>
            <th>Entry Price</th><th>Exit Price</th><th>Quantity</th><th>Leverage</th>
            <th>Gross PnL</th><th>Fees</th><th>Net PnL</th><th>Net PnL %</th>
            <th>Strategy</th><th>Exit Reason</th><th>Result</th>
        </tr>
        </thead>
        <tbody>
        {full_log_rows}
        </tbody>
    </table>
    </div>
</div>

<!-- Export modal -->
<div id="exportModal" class="modal-overlay" onclick="if(event.target===this) closeExportModal()">
  <div class="modal-content">
    <h2>Export Trades</h2>
    <div class="modal-filters">
      <div>
        <label>Date range</label>
        <div class="date-range">
          <input type="date" id="exportFromDate" oninput="applyExportFilters()">
          <span style="color:#666;">to</span>
          <input type="date" id="exportToDate" oninput="applyExportFilters()">
        </div>
      </div>
      <div>
        <label>Symbol</label>
        <select id="exportSymbol" onchange="applyExportFilters()">
          <option value="">All symbols</option>
        </select>
      </div>
      <div>
        <label>Bot</label>
        <select id="exportBot" onchange="applyExportFilters()">
          <option value="">All bots</option>
          <option value="momentum">Momentum (4H/1D)</option>
          <option value="whale">Whale Tracker</option>
        </select>
      </div>
    </div>
    <div class="modal-preview-header">
      <div class="modal-preview-count">Matching trades: <b id="exportCount">0</b></div>
      <div style="color:#a0a0a8;font-size:0.85em;">Net PnL: <b id="exportNetPnl">$0.00</b></div>
    </div>
    <div class="modal-preview">
      <table>
        <thead>
        <tr>
          <th>Closed</th><th>Symbol</th><th>Direction</th><th>Strategy</th><th style="text-align:right;">Net PnL</th>
        </tr>
        </thead>
        <tbody id="exportPreviewBody"></tbody>
      </table>
    </div>
    <div class="modal-actions">
      <span id="exportSpinner" class="spinner-bitcoin" style="display:none;">₿</span>
      <button class="btn-secondary" onclick="closeExportModal()">Cancel</button>
      <button id="exportDownloadBtn" class="btn-primary" onclick="downloadExport()">Download CSV</button>
    </div>
  </div>
</div>

</div><!-- end tab-tradelog -->

<!-- ═══════════════════ TAB 3: YEARLY PROJECTION ═══════════════════ -->
<div id="tab-projection" class="tab-content">

<div class="stats-bar">
    <div class="stat-card">
        <div class="label">Starting Capital</div>
        <div class="value">${INITIAL_CAPITAL:,.0f}</div>
    </div>
    <div class="stat-card">
        <div class="label">Notional / Trade</div>
        <div class="value">${MARGIN_PER_TRADE * DEFAULT_LEVERAGE:,.0f}</div>
    </div>
    <div class="stat-card">
        <div class="label">Projected Annual PnL</div>
        <div class="value {proj_total_class}">${proj_total_annual:+,.2f}</div>
    </div>
    <div class="stat-card">
        <div class="label">Projected Annual %</div>
        <div class="value {proj_total_class}">{proj_total_pct:+.2f}%</div>
    </div>
    <div class="stat-card">
        <div class="label">Trades / Year</div>
        <div class="value">{proj_trades_per_year:.1f}</div>
    </div>
</div>

<div class="section">
    <h2>Per-Strategy Annual Projection</h2>
    <p style="opacity:0.6;font-size:0.85em;margin-top:-8px;">
        Extrapolated from {BACKTEST_YEARS}-year BINANCE backtest (Dec 2020 → Apr 2026),
        scaled to live sizing: ${MARGIN_PER_TRADE:.0f} margin × {DEFAULT_LEVERAGE}x = ${MARGIN_PER_TRADE * DEFAULT_LEVERAGE:.0f} notional (vs ${BACKTEST_CAPITAL * BACKTEST_QTY_PCT/100:.0f} backtest).
        Scale factor {projection['scale']:.2f}× applied. Assumes steady regime — real returns will deviate.
    </p>
    <div class="scroll-table">
    <table>
        <tr>
            <th>Strategy</th>
            <th>Symbol</th>
            <th>TF</th>
            <th>PF</th>
            <th>Trades/Yr</th>
            <th>Annual % (Backtest)</th>
            <th>Projected Annual $</th>
            <th>Max DD</th>
        </tr>
        {projection_rows}
    </table>
    </div>
</div>

<div class="section">
    <h2>Disclaimer</h2>
    <p style="opacity:0.7;line-height:1.6;">
        These projections are <strong>not guarantees</strong>. Profit factor and trade counts come from historical
        BINANCE data and assume the bot's filters match the TradingView Pine backtest exactly. Variables that can cause
        divergence: exchange slippage/funding/latency, regime changes (2020-2024 had strong trends),
        strategies running simultaneously on finite capital ({MAX_POSITIONS} concurrent max), and tax/withdrawal timing.
        Treat the <em>ranking</em> as more reliable than the <em>absolute $</em>.
    </p>
</div>

</div><!-- end tab-projection -->

<!-- ═══════════════════ TAB 4: WHALE BOT ═══════════════════ -->
<div id="tab-whale" class="tab-content">

<div class="stats-bar">
    <div class="stat-card">
        <div class="label">Whale Trades</div>
        <div class="value">{whale_trade_count}</div>
    </div>
    <div class="stat-card">
        <div class="label">Win Rate</div>
        <div class="value">{whale_win_rate}%</div>
    </div>
    <div class="stat-card">
        <div class="label">Profit Factor</div>
        <div class="value">{whale_pf:.2f}</div>
    </div>
    <div class="stat-card">
        <div class="label">Net PnL</div>
        <div class="value {whale_net_class}">${whale_net_pnl:+,.2f}</div>
    </div>
    <div class="stat-card">
        <div class="label">Open Positions</div>
        <div class="value">{whale_open_count}</div>
    </div>
</div>

<div class="section">
    <h2>Open Whale Positions</h2>
    <p style="opacity:0.6;font-size:0.85em;margin-top:-8px;">
        Live positions opened by the whale-tracking bot. Exits trigger on SL, TP, or smart-money signal flip.
    </p>
    <div class="scroll-table">
    <table>
        <tr>
            <th>Coin</th>
            <th>Symbol</th>
            <th>Direction</th>
            <th style="text-align:right;">Entry</th>
            <th style="text-align:right;">Qty</th>
            <th style="text-align:right;">SL</th>
            <th style="text-align:right;">TP</th>
            <th>Signal Type</th>
            <th style="text-align:right;">Margin</th>
        </tr>
        {whale_positions_rows}
    </table>
    </div>
</div>

<div class="section">
    <h2>Latest Whale Signal Scan</h2>
    <p style="opacity:0.6;font-size:0.85em;margin-top:-8px;">
        Last scan at <b>{whale_signals_ts}</b>.
        <span class="badge badge-yellow">DIVERGENCE</span> = smart vs rekt opposite (higher conviction, 1.5× size).
        <span class="badge badge-blue">CONSENSUS</span> = ≥80% smart-money agreement. Crowded-trade + edge-decay filters applied.
    </p>
    <div class="scroll-table">
    <table style="font-size:0.9em;">
        <tr>
            <th>Coin</th>
            <th>WEEX Symbol</th>
            <th>Signal</th>
            <th>Direction</th>
            <th style="text-align:right;">Confidence</th>
            <th style="text-align:right;">Smart Long%</th>
            <th style="text-align:right;">Smart Short%</th>
            <th style="text-align:right;">#Traders</th>
            <th style="text-align:right;" title="HL funding rate annualized. Green = confirms our direction; red = extremely crowded.">Funding</th>
            <th style="text-align:right;" title="Whale liquidation clusters near entry. Red = adverse (stop-hunt risk); green = fuel in our favor.">Liq Risk / Fuel</th>
            <th style="text-align:right;" title="Position changes since last poll: new entries, size growth, exits.">Recency</th>
            <th>Reasoning</th>
        </tr>
        {whale_signals_rows}
    </table>
    </div>
</div>

<div class="section">
    <h2>How this bot trades</h2>
    <p style="opacity:0.75;line-height:1.6;">
        <b>Source:</b> Hyperliquid public API (leaderboard + clearinghouseState). 15-min poll cadence.<br>
        <b>Signal:</b> Smart-money basket (top 20 by all-time PnL) vs rekt basket (worst 20 monthly, $1k+ accounts).
        A coin classifies as <b>DIVERGENCE</b> when ≥70% of smart money and ≥70% of rekt sit on opposite sides.
        It classifies as <b>CONSENSUS</b> when ≥80% of smart money agrees, regardless of rekt.<br>
        <b>Filters:</b> min 5 smart-money traders per coin · smart basket must be winning on the coin (edge-decay guard) ·
        skip if both smart AND rekt are crowded on the same side · coin must be listed on WEEX.<br>
        <b>Sizing:</b> Consensus $500 notional ($50 × 10x), Divergence $750 ($75 × 10x).
        Stops: 1.5× ATR(14,4H), Targets: 3.0× ATR (2R reward:risk). Early exit if smart-money consensus drops below 55%.<br>
        <b>Caveat:</b> Whale edge decays. Expect 30–80% Y1, 10–30% Y2, 0–15% Y3 without re-tuning. Retune thresholds quarterly.
    </p>
</div>

</div><!-- end tab-whale -->

<script>
// ── Tab Switching ──
function switchTab(tabName) {{
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    document.getElementById('tab-' + tabName).classList.add('active');
    event.target.classList.add('active');
}}

// ── Export Trades modal ──
const ALL_TRADES = {trades_json};

function _isWhaleTrade(t) {{
    const s = (t.strategy || '').toString();
    return s.startsWith('Whale Track') || s.startsWith('Whale ');
}}

function _filteredTrades() {{
    const fromDate = document.getElementById('exportFromDate').value;
    const toDate   = document.getElementById('exportToDate').value;
    const symbol   = document.getElementById('exportSymbol').value;
    const bot      = document.getElementById('exportBot').value;
    return ALL_TRADES.filter(t => {{
        const closed = (t.date_closed || '').slice(0, 10);
        if (fromDate && closed && closed < fromDate) return false;
        if (toDate   && closed && closed > toDate)   return false;
        if (symbol && t.symbol !== symbol) return false;
        if (bot === 'whale'    && !_isWhaleTrade(t)) return false;
        if (bot === 'momentum' &&  _isWhaleTrade(t)) return false;
        return true;
    }});
}}

function applyExportFilters() {{
    const filtered = _filteredTrades();
    const body = document.getElementById('exportPreviewBody');
    const count = document.getElementById('exportCount');
    const netEl = document.getElementById('exportNetPnl');

    count.textContent = filtered.length;
    let totalPnl = 0;
    body.innerHTML = '';
    const previewLimit = 100;
    filtered.slice(0, previewLimit).forEach(t => {{
        const pnl = parseFloat(t.net_pnl) || 0;
        totalPnl += pnl;
        const cls = pnl > 0 ? 'green' : (pnl < 0 ? 'red' : '');
        const tr = document.createElement('tr');
        tr.innerHTML =
            '<td>' + (t.date_closed || '').slice(0, 16) + '</td>' +
            '<td>' + (t.symbol || '') + '</td>' +
            '<td>' + (t.direction || '') + '</td>' +
            '<td style="opacity:0.8;">' + (t.strategy || '') + '</td>' +
            '<td style="text-align:right;" class="' + cls + '">$' + pnl.toFixed(2) + '</td>';
        body.appendChild(tr);
    }});
    // Sum the FULL filtered set (not just preview-limited rows) for the net display
    const fullPnl = filtered.reduce((a, t) => a + (parseFloat(t.net_pnl) || 0), 0);
    netEl.textContent = '$' + fullPnl.toFixed(2);
    netEl.className = fullPnl >= 0 ? 'green' : 'red';
    if (filtered.length > previewLimit) {{
        const tr = document.createElement('tr');
        tr.innerHTML = '<td colspan="5" style="text-align:center;opacity:0.6;font-style:italic;">… and ' + (filtered.length - previewLimit) + ' more (full set will be in CSV)</td>';
        body.appendChild(tr);
    }}
    document.getElementById('exportDownloadBtn').disabled = (filtered.length === 0);
}}

function _populateSymbolDropdown() {{
    const select = document.getElementById('exportSymbol');
    // Clear existing (except the first "All" option)
    while (select.options.length > 1) select.remove(1);
    const symbols = [...new Set(ALL_TRADES.map(t => t.symbol).filter(Boolean))].sort();
    symbols.forEach(s => {{
        const opt = document.createElement('option');
        opt.value = s; opt.textContent = s;
        select.appendChild(opt);
    }});
}}

function openExportModal() {{
    if (!ALL_TRADES.length) {{ alert('No trades to export yet.'); return; }}
    _populateSymbolDropdown();
    document.getElementById('exportFromDate').value = '';
    document.getElementById('exportToDate').value   = '';
    document.getElementById('exportSymbol').value   = '';
    document.getElementById('exportBot').value      = '';
    applyExportFilters();
    document.getElementById('exportModal').classList.add('show');
}}

function closeExportModal() {{
    document.getElementById('exportModal').classList.remove('show');
    document.getElementById('exportSpinner').style.display = 'none';
}}

function downloadExport() {{
    const filtered = _filteredTrades();
    if (!filtered.length) {{ alert('No trades match these filters.'); return; }}

    // Show the bitcoin spinner during the build (fast, but gives clear feedback)
    const spinner = document.getElementById('exportSpinner');
    const btn = document.getElementById('exportDownloadBtn');
    spinner.style.display = 'inline-block';
    btn.disabled = true;

    setTimeout(() => {{
        try {{
            const headers = ['Trade #','Date Opened','Date Closed','Symbol','Direction','Entry Price','Exit Price','Quantity','Leverage','Gross PnL','Fees','Net PnL','Net PnL %','Strategy','Bot','Entry Reason','Exit Reason','Notes','Result'];
            const rows = filtered.map((t, i) => {{
                const ep = parseFloat(t.entry_price) || 0;
                const xp = parseFloat(t.exit_price) || 0;
                const qty = parseFloat(t.quantity) || 0;
                const lev = parseInt(t.leverage) || 1;
                const pnl = parseFloat(t.net_pnl) || 0;
                const gross = parseFloat(t.gross_pnl) || 0;
                const fees = parseFloat(t.fees) || 0;
                const margin = ep * qty / lev;
                const pct = margin > 0 ? (pnl / margin * 100).toFixed(1) + '%' : '0%';
                const result = pnl > 0 ? 'WIN' : (pnl < 0 ? 'LOSS' : 'FLAT');
                const botName = _isWhaleTrade(t) ? 'Whale' : 'Momentum';
                return [i+1, t.date_opened||'', t.date_closed||'', t.symbol||'', t.direction||'', ep, xp, qty, lev+'x', gross.toFixed(2), fees.toFixed(2), pnl.toFixed(2), pct, t.strategy||'', botName, t.entry_reason||'', t.exit_reason||'', t.notes||'', result];
            }});
            let csv = '\\uFEFF' + headers.join(',') + '\\n';
            rows.forEach(r => {{ csv += r.map(v => '\"' + String(v).replace(/\"/g, '\"\"') + '\"').join(',') + '\\n'; }});
            const blob = new Blob([csv], {{ type: 'text/csv;charset=utf-8;' }});
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = 'Trade_Log_' + new Date().toISOString().slice(0,10) + '.csv';
            link.click();
        }} finally {{
            // Brief delay so the user sees the spinner spin at least once before close
            setTimeout(() => {{ closeExportModal(); btn.disabled = false; }}, 600);
        }}
    }}, 50);
}}

// ── Daily PnL Chart ──
const dailyLabels = {daily_labels};
const dailyValues = {daily_values};
const dailyColors = dailyValues.map(v => v >= 0 ? '#00c853' : '#e5173f');
new Chart(document.getElementById('dailyPnlChart'), {{
    type: 'bar',
    data: {{
        labels: dailyLabels,
        datasets: [{{ data: dailyValues, backgroundColor: dailyColors, borderRadius: 4 }}]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: ctx => '$' + ctx.parsed.y.toFixed(2) }} }} }},
        scales: {{
            x: {{ ticks: {{ color: '#a0a0a8', maxRotation: 45 }}, grid: {{ display: false }} }},
            y: {{ ticks: {{ color: '#a0a0a8', callback: v => '$' + v }}, grid: {{ color: '#1c1c2e' }} }}
        }}
    }}
}});

// ── Equity Curve ──
const eqData = {equity_curve};
const eqLabels = eqData.map((_, i) => i === 0 ? 'Start' : 'Trade ' + i);
const ctx2 = document.getElementById('equityChart').getContext('2d');
const gradient = ctx2.createLinearGradient(0, 0, 0, 300);
gradient.addColorStop(0, 'rgba(0, 212, 212, 0.30)');
gradient.addColorStop(1, 'rgba(0, 212, 212, 0.0)');
new Chart(ctx2, {{
    type: 'line',
    data: {{
        labels: eqLabels,
        datasets: [{{ data: eqData, borderColor: '#00d4d4', backgroundColor: gradient, fill: true, tension: 0.3, pointRadius: 0 }}]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
            x: {{ ticks: {{ color: '#a0a0a8', maxTicksLimit: 10 }}, grid: {{ display: false }} }},
            y: {{ ticks: {{ color: '#a0a0a8', callback: v => '$' + v.toLocaleString() }}, grid: {{ color: '#1c1c2e' }} }}
        }}
    }}
}});

// ── Allocation Chart ──
const allocLabels = {alloc_labels};
const allocValues = {alloc_values};
const allocColors = ['#00d4d4', '#00c853', '#d040f0', '#1565c0', '#e5173f', '#c8c820', '#00e676', '#9c27b0', '#607d8b'];
new Chart(document.getElementById('allocChart'), {{
    type: 'doughnut',
    data: {{
        labels: allocLabels,
        datasets: [{{ data: allocValues, backgroundColor: allocColors.slice(0, allocLabels.length), borderWidth: 0 }}]
    }},
    options: {{
        responsive: true,
        plugins: {{
            legend: {{ position: 'right', labels: {{ color: '#f0f0f0', padding: 12 }} }},
            tooltip: {{ callbacks: {{ label: ctx => ctx.label + ': $' + ctx.parsed.toFixed(2) }} }}
        }}
    }}
}});
</script>

</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("Dashboard written to %s", path)


def build_dashboard(executor, state: dict) -> None:
    """Convenience: gather data and generate dashboard."""
    data = gather_dashboard_data(executor, state)
    generate_dashboard(data)
