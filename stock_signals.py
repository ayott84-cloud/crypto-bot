"""Module 2 Phase S2 — daily-bar equity sleeve signals.

Three sleeves, all LONG/FLAT by design. Refusing to short is a scope
decision that buys a lot: no Reg SHO short-sale restriction to detect
(a falling stock's shorts get rejected exactly when a strategy most
wants them), no locate management, no hard-to-borrow fees, and no
unbounded-loss tail.

  StockTrend  Faber time-series momentum. Hold while the close is above
              its 200-day SMA, else stand in cash. Tier-A evidence
              (Faber 2007; Quantpedia replication Sharpe ~1.06
              1973-2008). Its job is drawdown compression, not return —
              it does NOT beat buy-and-hold in a bull decade, and any
              backtest saying otherwise is measuring luck or lookahead.

  StockDual   Antonacci dual momentum, run as an ENSEMBLE. ReSolve
              tested 1,226 specifications and found the canonical 12/1
              spec beat only 61% of its own siblings — indistinguishable
              from luck — while the 5-year return spread between a lucky
              and an unlucky spec averaged 64 percentage points. So the
              vote is taken across several lookbacks and the DISPERSION
              is reported, not hidden. The cash leg is mandatory: 2022
              broke classic GEM when stocks and bonds fell together and
              the "safe harbour" was itself the loser.

  StockRev    IBS / RSI(2) short-term reversion, index ETFs only. Tier-B
              and openly decaying since ~2013; kept for its negative
              correlation to the trend sleeve rather than its own
              return. The 200-day regime filter is what separates
              "buy the dip" from "catch the knife".

CONVENTIONS (shared with the crypto analyzers so replays import LIVE
code and drift is impossible):
  * analyze_*_entry returns {would_enter, direction, blocked_by,
    filters, values} — the same dict signals.analyze_entry_signal
    returns, which is what feeds the dashboard's why-silent panel.
  * Every read is of the last COMPLETED bar (iloc[-2]). The final row of
    a live frame is still forming; acting on it repaints the signal
    intraday and makes the backtest unreproducible.
"""

from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

# The last COMPLETED bar. iloc[-1] is still forming in live data.
_COMPLETED = -2


def _blank(extra: Optional[dict] = None) -> dict:
    out = {"would_enter": False, "direction": None, "blocked_by": None,
            "filters": {}, "values": {}}
    if extra:
        out.update(extra)
    return out


# ─── Indicators ───────────────────────────────────────────────────────────

def ibs(df: pd.DataFrame) -> pd.Series:
    """Internal Bar Strength: where the close sits inside the bar range.

    0 = closed on the low (capitulation), 1 = closed on the high. A
    limit-locked bar has high == low; that range is 0 and the naive
    division yields NaN, which then propagates silently into the signal
    — so a zero-range bar is defined as neutral 0.5.
    """
    rng = (df["high"] - df["low"]).astype(float)
    out = (df["close"].astype(float) - df["low"].astype(float)) / rng
    return out.where(rng > 0, 0.5).clip(0.0, 1.0)


def rsi(close: pd.Series, period: int = 2) -> pd.Series:
    """Wilder RSI. Short periods (2-3) are the mean-reversion variant;
    the standard 14 is far too smooth to mark a one-bar washout."""
    delta = close.astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, float("nan"))
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.fillna(100.0).clip(0.0, 100.0)


def total_return(close: pd.Series, lookback_bars: int) -> Optional[float]:
    """Simple total return over `lookback_bars` completed bars."""
    if len(close) < lookback_bars + 2:
        return None
    now = float(close.iloc[_COMPLETED])
    then = float(close.iloc[_COMPLETED - lookback_bars])
    if then == 0:
        return None
    return (now / then - 1.0) * 100.0


# ─── StockTrend ───────────────────────────────────────────────────────────

def analyze_trend_entry(df: pd.DataFrame, cfg: dict) -> dict:
    """Hold while the last completed close is above its SMA."""
    period = int(cfg.get("sma_period", 200))
    if df is None or len(df) < period + 2:
        return _blank({"blocked_by": "insufficient_history",
                        "values": {"bars": 0 if df is None else len(df),
                                    "need": period + 2}})
    close = df["close"].astype(float)
    sma = close.rolling(period).mean()
    c = float(close.iloc[_COMPLETED])
    s = float(sma.iloc[_COMPLETED])
    if pd.isna(s):
        return _blank({"blocked_by": "insufficient_history"})

    above = c > s
    return {
        "would_enter": bool(above),
        "direction": "LONG" if above else None,
        "blocked_by": None if above else "below_sma",
        "filters": {"above_sma": bool(above)},
        "values": {"close": c, "sma": round(s, 6),
                    "distance_pct": round((c / s - 1.0) * 100.0, 3)},
    }


def check_trend_exit(df: pd.DataFrame, cfg: dict) -> Optional[str]:
    """Exit when the close falls back below the SMA. Symmetric with
    entry on purpose — an asymmetric band would be a second, untested
    parameter."""
    period = int(cfg.get("sma_period", 200))
    if df is None or len(df) < period + 2:
        return None
    close = df["close"].astype(float)
    sma = close.rolling(period).mean()
    c, s = float(close.iloc[_COMPLETED]), float(sma.iloc[_COMPLETED])
    if pd.isna(s):
        return None
    return None if c > s else "Below SMA"


# ─── StockDual ────────────────────────────────────────────────────────────

_TRADING_DAYS_PER_MONTH = 21


def dual_momentum_vote(frames: Dict[str, pd.DataFrame], cfg: dict) -> dict:
    """Ensemble dual momentum across several lookbacks.

    Returns the winning symbol plus the FULL per-lookback breakdown and
    an agreement ratio. Reporting only the winner would reduce an
    ensemble back to a single spec with extra steps — and specification
    risk is the documented failure mode of this strategy family.

    Absolute-momentum leg: the chosen risk asset must also beat the cash
    proxy. When it does not, the defensive pick is whichever of the safe
    (bond) and cash assets is itself stronger — because in 2022 bonds
    fell alongside stocks and a hardcoded flight to bonds was a loss.
    """
    risk = list(cfg.get("risk_assets", []))
    safe = cfg.get("safe_asset")
    cash = cfg.get("cash_asset")
    lookbacks = list(cfg.get("lookbacks_months", [3, 6, 9, 12]))

    needed = [s for s in risk + [safe, cash] if s]
    missing = [s for s in needed if s not in (frames or {})]
    if missing:
        return {"winner": None, "blocked_by": "missing_data",
                 "missing": missing, "per_lookback": {},
                 "ensemble_agreement": 0.0, "absolute_momentum_ok": False}

    max_bars = max(lookbacks) * _TRADING_DAYS_PER_MONTH
    if any(len(frames[s]) < max_bars + 2 for s in needed):
        return {"winner": None, "blocked_by": "insufficient_history",
                 "per_lookback": {}, "ensemble_agreement": 0.0,
                 "absolute_momentum_ok": False}

    per_lookback: Dict[int, str] = {}
    for lb in lookbacks:
        bars = lb * _TRADING_DAYS_PER_MONTH
        scores = {s: total_return(frames[s]["close"], bars) for s in risk}
        scores = {s: v for s, v in scores.items() if v is not None}
        if not scores:
            continue
        best = max(scores, key=scores.get)
        cash_ret = total_return(frames[cash]["close"], bars) or 0.0
        if scores[best] > cash_ret:
            per_lookback[lb] = best
        else:
            # Defensive: pick the stronger of bonds vs cash, never assume
            # bonds are safe (2022).
            safe_ret = total_return(frames[safe]["close"], bars)
            per_lookback[lb] = (safe if safe_ret is not None
                                 and safe_ret > cash_ret else cash)

    if not per_lookback:
        return {"winner": None, "blocked_by": "insufficient_history",
                 "per_lookback": {}, "ensemble_agreement": 0.0,
                 "absolute_momentum_ok": False}

    votes: Dict[str, int] = {}
    for sym in per_lookback.values():
        votes[sym] = votes.get(sym, 0) + 1
    winner = max(votes, key=votes.get)
    agreement = votes[winner] / len(per_lookback)

    return {
        "winner": winner,
        "blocked_by": None,
        "per_lookback": per_lookback,
        "votes": votes,
        "ensemble_agreement": round(agreement, 4),
        "absolute_momentum_ok": winner in risk,
        "defensive": winner not in risk,
    }


# ─── StockRev ─────────────────────────────────────────────────────────────

def analyze_reversion_entry(df: pd.DataFrame, cfg: dict) -> dict:
    """Oversold pullback inside an established uptrend.

    Filter order matters and is fixed: regime first, oversold second.
    `blocked_by` is the FIRST failing gate, which is what makes the
    dashboard's why-silent panel and fleet_review's blocker histogram
    interpretable.
    """
    sma_period = int(cfg.get("sma_period", 200))
    if df is None or len(df) < sma_period + 2:
        return _blank({"blocked_by": "insufficient_history"})

    close = df["close"].astype(float)
    sma = close.rolling(sma_period).mean()
    c = float(close.iloc[_COMPLETED])
    s = float(sma.iloc[_COMPLETED])
    if pd.isna(s):
        return _blank({"blocked_by": "insufficient_history"})

    above_sma = c > s
    ibs_v = float(ibs(df).iloc[_COMPLETED])
    rsi_v = float(rsi(close, int(cfg.get("rsi_period", 2))).iloc[_COMPLETED])
    ibs_ok = ibs_v < float(cfg.get("ibs_threshold", 0.2))
    rsi_ok = rsi_v < float(cfg.get("rsi_threshold", 10.0))
    oversold = ibs_ok or rsi_ok

    values = {"close": c, "sma": round(s, 6), "ibs": round(ibs_v, 4),
               "rsi": round(rsi_v, 2)}
    filters = {"above_sma": above_sma, "ibs": ibs_ok, "rsi": rsi_ok}

    if not above_sma:
        return _blank({"blocked_by": "below_sma", "filters": filters,
                        "values": values})
    if not oversold:
        return _blank({"blocked_by": "not_oversold", "filters": filters,
                        "values": values})
    return {"would_enter": True, "direction": "LONG", "blocked_by": None,
             "filters": filters, "values": values}


def check_reversion_exit(df: pd.DataFrame, cfg: dict, bars_held: int,
                           entry_price: float) -> Optional[str]:
    """Fixed precedence: mean reached -> momentum spent -> time.

    Two conditions are often true on the same bar; without a fixed order
    the journal's exit-reason distribution becomes noise and the
    runbook's 'SL>60%' style diagnostics stop meaning anything.
    """
    if df is None or len(df) < 3:
        return None
    close = df["close"].astype(float)
    c = float(close.iloc[_COMPLETED])

    exit_sma_p = int(cfg.get("exit_sma_period", 5))
    if len(close) >= exit_sma_p + 2:
        exit_sma = close.rolling(exit_sma_p).mean().iloc[_COMPLETED]
        if not pd.isna(exit_sma) and c > float(exit_sma):
            return "Above SMA"

    rsi_v = float(rsi(close, int(cfg.get("rsi_period", 2))).iloc[_COMPLETED])
    if rsi_v > float(cfg.get("rsi_exit", 70.0)):
        return "RSI Exit"

    if bars_held >= int(cfg.get("max_hold_bars", 10)):
        return "Time Stop"
    return None
