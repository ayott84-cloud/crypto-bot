"""Phase H — risk-adjusted performance metrics.

All metrics are pure functions of their inputs (trade lists or equity curves).
Dashboard composes them via dashboard._compute_metrics(). Each is testable
in isolation against tests/test_metrics.py.

References:
  - Sortino:    OptimizedPortfolio 2026 — Sharpe / Sortino / Calmar compared
  - Calmar:     JournalPlus — Calmar Ratio (rolling-window variant)
  - Ulcer Idx:  PortfoliosLab — Ulcer Index
  - All:        XBTO — crypto-specific risk-adjusted metric guide
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Dict, List, Optional, Sequence


# ─── Sortino ────────────────────────────────────────────────────────────────

def sortino(pnls: Sequence[float], target: float = 0.0,
            trades_per_year: int = 72) -> float:
    """Annualized Sortino ratio.

    Sortino = mean(returns - target) / downside_deviation * sqrt(trades_per_year)
    where downside_deviation = sqrt(mean(min(0, r - target)^2)) over returns
    BELOW target only.

    Differs from Sharpe by ignoring upside vol — appropriate for strategies
    with positive skew (our momentum bot's PF ~5 has Sharpe undersold by
    standard volatility).

    Capped at +/-999 to avoid infinity on monotonic-up series.
    """
    if not pnls:
        return 0.0
    n = len(pnls)
    mean_return = sum(pnls) / n - target
    downside_sq = [(min(0.0, p - target)) ** 2 for p in pnls]
    downside_dev = math.sqrt(sum(downside_sq) / n)
    if downside_dev == 0:
        # No losses; ratio is infinite. Cap.
        return 999.0 if mean_return > 0 else 0.0
    ratio = mean_return / downside_dev * math.sqrt(trades_per_year)
    return max(-999.0, min(999.0, ratio))


# ─── Drawdown family ───────────────────────────────────────────────────────

def max_drawdown(equity_curve: Sequence[float]) -> float:
    """Peak-to-trough drawdown as a percentage of peak.

    Walks the curve tracking the running max and the largest fractional
    fall from that max. Returns 0 if curve is empty or monotonic up.
    """
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd_pct = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            dd = (peak - v) / peak * 100.0
            max_dd_pct = max(max_dd_pct, dd)
    return round(max_dd_pct, 2)


def _equity_from_pnls(pnls: Sequence[float], initial_equity: float) -> List[float]:
    """Cumulative equity curve starting from initial_equity."""
    eq = [initial_equity]
    running = initial_equity
    for p in pnls:
        running += p
        eq.append(running)
    return eq


def calmar(pnls: Sequence[float], initial_equity: float,
           days: int = 90) -> float:
    """Annualized return / max drawdown over the supplied window.

    Convention: returns 999 if max drawdown is zero (would be div-by-zero).
    Returns 0 if pnls empty.

    The `days` parameter is informational (annualizes return based on the
    window length); the caller is responsible for trimming pnls to the
    window first if a rolling-90d is desired.
    """
    if not pnls:
        return 0.0
    equity = _equity_from_pnls(pnls, initial_equity)
    total_return = (equity[-1] - initial_equity) / initial_equity
    if days <= 0:
        return 0.0
    annual_return = total_return * (365.0 / days)
    dd = max_drawdown(equity) / 100.0  # max_drawdown returns percent
    if dd == 0:
        return 999.0 if annual_return > 0 else 0.0
    return round(annual_return / dd, 2)


def ulcer_index(equity_curve: Sequence[float]) -> float:
    """Root-mean-square of percentage drawdowns from running peak.

    Captures drawdown DURATION + DEPTH simultaneously — a long shallow DD
    can score worse than a brief deep one because the squared depths
    accumulate over time underwater.
    """
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    dd_pcts_sq = []
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            dd_pct = (peak - v) / peak * 100.0
            dd_pcts_sq.append(dd_pct ** 2)
    if not dd_pcts_sq:
        return 0.0
    return round(math.sqrt(sum(dd_pcts_sq) / len(dd_pcts_sq)), 2)


def time_to_recovery(equity_curve: Sequence[float]) -> int:
    """Number of bars since the equity curve last set a new peak.

    0 means currently AT or above the all-time peak.
    Positive N means the curve has been underwater for N bars.

    Useful as an operational "should I pause this bot?" signal —
    sustained negative time-to-recovery is the deepest warning we have
    short of a hard kill switch.
    """
    if not equity_curve:
        return 0
    peak = equity_curve[0]
    last_peak_idx = 0
    for i, v in enumerate(equity_curve):
        if v >= peak:
            peak = v
            last_peak_idx = i
    return len(equity_curve) - 1 - last_peak_idx


# ─── Annualized Sharpe with observed trade frequency ──────────────────────

def annualized_sharpe(pnls: Sequence[float], days_observed: int) -> float:
    """Sharpe ratio annualized using the OBSERVED trade frequency.

    Replaces dashboard.py:118's hardcoded `(72 ** 0.5)` (which assumed
    6 trades/month) with: trades_per_year = len(pnls) * 365 / days_observed.
    For a 30-day window with 6 trades, this gives 73 trades/year — matches
    the old constant. For a 365-day window with 6 trades it gives 6/yr,
    a 3.5x lower annualization factor. The peer-review flagged this as
    inaccurate across bots with different cadences.
    """
    if not pnls or days_observed <= 0:
        return 0.0
    if len(pnls) < 2:
        return 0.0
    mean_p = statistics.mean(pnls)
    std_p = statistics.stdev(pnls)
    if std_p == 0:
        return 999.0 if mean_p > 0 else 0.0
    trades_per_year = len(pnls) * 365.0 / days_observed
    sharpe = mean_p / std_p * math.sqrt(trades_per_year)
    return round(max(-999.0, min(999.0, sharpe)), 2)


# ─── Per-regime expectancy ─────────────────────────────────────────────────

def per_regime_expectancy(trades: List[dict]) -> Dict[str, dict]:
    """Bucket closed trades by regime_at_entry tag and report per-bucket stats.

    Skips trades without a populated regime_at_entry — supports the going-
    forward-only data we'll get after Phase H ships (we deferred B.3b
    backfill, so historical trades have no regime tag yet).

    Each bucket gets: count, expectancy (mean PnL), win_rate, total_pnl.
    """
    buckets: Dict[str, List[float]] = defaultdict(list)
    for t in trades:
        regime = (t.get("regime_at_entry") or "").strip()
        if not regime:
            continue
        pnl = float(t.get("net_pnl") or 0)
        buckets[regime].append(pnl)

    out = {}
    for regime, pnls in buckets.items():
        wins = sum(1 for p in pnls if p > 0)
        out[regime] = {
            "count":      len(pnls),
            "expectancy": round(sum(pnls) / len(pnls), 2),
            "win_rate":   round(wins / len(pnls) * 100.0, 2),
            "total_pnl":  round(sum(pnls), 2),
        }
    return out
