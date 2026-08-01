"""Module 2 Phase S2 — honest multi-decade validation of the stock sleeves.

Runs each sleeve over its full available history, then applies the
PRE-REGISTERED gates. Registering them here, in code, before seeing a
number is the point: the crypto module's most expensive lesson was that
a threshold chosen after the fact is not a threshold.

WHY THE GATES DIFFER BY SLEEVE
  PF >= 1.3 is the crypto bar and is meaningless for S1/S2. A trend
  sleeve makes ~2-6 round trips per asset per year, so twenty years is
  perhaps 60 trades and PF is decided by a handful of them. Those
  sleeves are judged on Sharpe, max drawdown, and whether the result
  survives the multiple-testing haircut. The reversion sleeve trades
  often enough for PF to mean something, so it keeps that gate.

  Every sleeve additionally faces DSR (selection bias + non-normality)
  and PBO (does picking the in-sample winner generalize at all).

Run (droplet):
  venv/bin/python tools/validate_stock_sleeves.py            # all sleeves
  venv/bin/python tools/validate_stock_sleeves.py --sleeve trend
  venv/bin/python tools/validate_stock_sleeves.py --years 25 --refresh
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

import numpy as np
import pandas as pd

import market_calendar as mc
from tools import _equity_bars as eb
from tools import overfit_stats as ofs

# ─── Pre-registered gates ─────────────────────────────────────────────────

GATES = {
    "trend": {"sharpe_min": 0.50, "max_dd_max": 25.0, "dsr_min": ofs.DSR_PASS,
               "pbo_max": 0.35, "min_trades": 20},
    "dual":  {"sharpe_min": 0.50, "max_dd_max": 25.0, "dsr_min": ofs.DSR_PASS,
               "pbo_max": 0.35, "min_trades": 15},
    "rev":   {"pf_min": 1.30, "max_dd_max": 20.0, "dsr_min": ofs.DSR_PASS,
               "pbo_max": 0.35, "min_trades": 50},
}

# Spec grids for the PBO sweep. PBO asks whether SELECTING among specs
# generalizes, so the grid must be the set we would actually have chosen
# from — not a token two-point sweep.
_TREND_GRID = [150, 175, 200, 225, 250]
_REV_GRID = [(0.15, 5.0), (0.20, 10.0), (0.25, 15.0), (0.30, 20.0)]


def _bar_returns(df: pd.DataFrame, trades, cost_pct: float) -> np.ndarray:
    """Per-bar strategy returns from a replay's trades.

    A per-TRADE series cannot be used for PBO: different specs produce
    different trade counts and the columns would not align. A per-bar
    series is the same length for every spec, which is what CSCV needs.
    """
    close = df["close"].astype(float).to_numpy()
    out = np.zeros(len(close), dtype=float)
    bar_ret = np.zeros(len(close), dtype=float)
    bar_ret[1:] = (close[1:] / close[:-1] - 1.0) * 100.0
    for t in trades:
        lo, hi = int(t.entry_bar) + 1, int(t.exit_bar)
        if hi >= lo:
            out[lo:hi + 1] = bar_ret[lo:hi + 1]
        if 0 <= int(t.exit_bar) < len(out):
            out[int(t.exit_bar)] -= cost_pct        # charge at exit
    return out


def _fetch(symbols, years: int, refresh: bool, provider: str) -> dict:
    end = date.today()
    start = end - timedelta(days=int(years * 365.25) + 30)
    frames = {}
    for sym in symbols:
        cached = None if refresh else eb.load_cache(sym, "1d", provider)
        if cached is not None and len(cached):
            frames[sym] = cached
            print(f"  {sym:6s} cache  {len(cached):5d} bars  "
                   f"{cached.index[0].date()} -> {cached.index[-1].date()}")
            continue
        try:
            df = eb.fetch_daily(sym, start, end, provider=provider)
        except eb.CredentialsMissing as e:
            print(f"\nCREDENTIALS: {e}")
            return {}
        except Exception as e:  # noqa: BLE001
            print(f"  {sym:6s} FETCH FAILED: {e}")
            continue
        if len(df):
            eb.save_cache(df, sym, "1d", provider)
            frames[sym] = df
            flag = " INCOMPLETE" if df.attrs.get("incomplete") else ""
            print(f"  {sym:6s} fetch  {len(df):5d} bars  "
                   f"{df.index[0].date()} -> {df.index[-1].date()}{flag}")
        else:
            print(f"  {sym:6s} EMPTY")
    return frames


def _fmt_gate(ok: bool) -> str:
    return "PASS" if ok else "fail"


def validate_trend(frames, cost_pct=None) -> list:
    from tools.backtest_replay import replay_stock_trend
    from stock_config import STOCK_TREND_ASSETS
    rows = []
    for name, cfg in STOCK_TREND_ASSETS.items():
        sym = cfg["symbol"]
        if sym not in frames:
            continue
        df = frames[sym]
        rep = replay_stock_trend(name, cfg, pre_fetched_df=df,
                                   round_trip_cost_pct=cost_pct)
        pnls = [t.pnl_pct for t in rep.trades]
        cost = cost_pct if cost_pct is not None else 0.05

        # PBO over the SMA-period grid — the specs we would have chosen from
        cols = []
        for p in _TREND_GRID:
            r = replay_stock_trend(name, {**cfg, "sma_period": p},
                                     pre_fetched_df=df,
                                     round_trip_cost_pct=cost_pct)
            cols.append(_bar_returns(df, r.trades, cost))
        pbo = ofs.pbo_cscv(np.column_stack(cols)) if len(cols) > 1 else {}

        dsr = ofs.deflated_sharpe(pnls, n_trials=len(_TREND_GRID))
        rows.append({"sleeve": "trend", "asset": name, "rep": rep,
                      "pnls": pnls, "dsr": dsr, "pbo": pbo,
                      "years": mc.bars_to_years(rep.bars_seen, "1d")})
    return rows


def validate_rev(frames, cost_pct=None) -> list:
    from tools.backtest_replay import replay_stock_rev
    from stock_config import STOCK_REV_ASSETS
    rows = []
    for name, cfg in STOCK_REV_ASSETS.items():
        sym = cfg["symbol"]
        if sym not in frames:
            continue
        df = frames[sym]
        rep = replay_stock_rev(name, cfg, pre_fetched_df=df,
                                 round_trip_cost_pct=cost_pct)
        pnls = [t.pnl_pct for t in rep.trades]
        cost = cost_pct if cost_pct is not None else 0.05

        cols = []
        for ibs_t, rsi_t in _REV_GRID:
            r = replay_stock_rev(name, {**cfg, "ibs_threshold": ibs_t,
                                          "rsi_threshold": rsi_t},
                                   pre_fetched_df=df,
                                   round_trip_cost_pct=cost_pct)
            cols.append(_bar_returns(df, r.trades, cost))
        pbo = ofs.pbo_cscv(np.column_stack(cols)) if len(cols) > 1 else {}

        dsr = ofs.deflated_sharpe(pnls, n_trials=len(_REV_GRID))
        rows.append({"sleeve": "rev", "asset": name, "rep": rep,
                      "pnls": pnls, "dsr": dsr, "pbo": pbo,
                      "years": mc.bars_to_years(rep.bars_seen, "1d")})
    return rows


def validate_dual(frames, cost_pct=None) -> list:
    from tools.backtest_replay import replay_stock_dual
    from stock_config import STOCK_DUAL_CONFIG as CFG
    needed = (list(CFG["risk_assets"]) + [CFG["safe_asset"], CFG["cash_asset"]])
    if any(s not in frames for s in needed):
        print(f"  dual: missing {[s for s in needed if s not in frames]}")
        return []
    sub = {s: frames[s] for s in needed}
    rep = replay_stock_dual("GEM", CFG, pre_fetched_frames=sub,
                              round_trip_cost_pct=cost_pct)
    pnls = [t.pnl_pct for t in rep.trades]

    # PBO over SINGLE-lookback specs — exactly the choice ReSolve showed
    # is indistinguishable from luck, which is why we ship the ensemble.
    anchor = sub[CFG["risk_assets"][0]]
    cost = cost_pct if cost_pct is not None else 0.05
    cols = []
    for lb in CFG["lookbacks_months"]:
        r = replay_stock_dual("GEM", {**CFG, "lookbacks_months": [lb]},
                                pre_fetched_frames=sub,
                                round_trip_cost_pct=cost_pct)
        cols.append(_bar_returns(anchor, r.trades, cost))
    pbo = ofs.pbo_cscv(np.column_stack(cols)) if len(cols) > 1 else {}

    dsr = ofs.deflated_sharpe(pnls, n_trials=len(CFG["lookbacks_months"]))
    return [{"sleeve": "dual", "asset": "GEM", "rep": rep, "pnls": pnls,
              "dsr": dsr, "pbo": pbo,
              "years": mc.bars_to_years(rep.bars_seen, "1d")}]


def report(rows) -> int:
    import metrics
    print("\n" + "=" * 78)
    print("STOCK SLEEVE VALIDATION — pre-registered gates")
    print("=" * 78)
    any_pass = False
    for r in rows:
        rep, pnls, g = r["rep"], r["pnls"], GATES[r["sleeve"]]
        yrs = max(r["years"], 0.01)
        days = int(yrs * mc.TRADING_DAYS_PER_YEAR)
        shp = metrics.annualized_sharpe(pnls, days_observed=max(days, 1),
                                          periods_per_year=mc.TRADING_DAYS_PER_YEAR)
        dd = rep.max_drawdown_pct
        pf = rep.profit_factor
        dsr, pbo = r["dsr"], r["pbo"]

        checks = []
        if "sharpe_min" in g:
            checks.append(("Sharpe", f"{shp:.2f}", shp >= g["sharpe_min"],
                            f">={g['sharpe_min']}"))
        if "pf_min" in g:
            checks.append(("PF", f"{pf:.2f}", pf >= g["pf_min"],
                            f">={g['pf_min']}"))
        checks.append(("maxDD", f"{dd:.1f}%", dd <= g["max_dd_max"],
                        f"<={g['max_dd_max']}%"))
        checks.append(("n", f"{rep.n_trades}", rep.n_trades >= g["min_trades"],
                        f">={g['min_trades']}"))
        checks.append(("DSR", f"{dsr.get('dsr', 0):.3f}",
                        dsr.get("dsr", 0) >= g["dsr_min"], f">={g['dsr_min']}"))
        if pbo.get("pbo") is not None:
            checks.append(("PBO", f"{pbo['pbo']:.2f}",
                            pbo["pbo"] <= g["pbo_max"], f"<={g['pbo_max']}"))

        verdict = "PASS" if all(c[2] for c in checks) else "FAIL"
        any_pass = any_pass or verdict == "PASS"
        warn = "  ⚠ " + "; ".join(rep.warnings) if rep.warnings else ""
        print(f"\n{r['sleeve']:6s} {r['asset']:12s} {yrs:5.1f}yr  "
               f"total={rep.total_return_pct:+8.1f}%  → {verdict}{warn}")
        print("       " + "  ".join(
            f"{n}={v}{'✓' if ok else '✗'}({bar})" for n, v, ok, bar in checks))

    print("\n" + "-" * 78)
    print("PF is deliberately NOT a gate for trend/dual: a few trades a year")
    print("means PF is decided by a handful of them. Those sleeves are judged")
    print("on Sharpe, drawdown, and whether the edge survives the")
    print("multiple-testing haircut (DSR) and spec-selection test (PBO).")
    print(f"Calendar backend: {mc.backend_note()}")
    return 0 if any_pass else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--years", type=int, default=25)
    ap.add_argument("--sleeve", choices=["trend", "dual", "rev", "all"],
                     default="all")
    ap.add_argument("--provider", default="tiingo", choices=["tiingo", "alpaca"])
    ap.add_argument("--cost-pct", type=float, default=None,
                     help="Round-trip cost override (stress arm: 0.10 = 2x "
                          "the 5bps ETF gate case)")
    ap.add_argument("--refresh", action="store_true",
                     help="Ignore the Parquet cache and refetch")
    args = ap.parse_args()

    from stock_config import all_symbols
    print(f"Credentials: {eb.credential_status()}")
    print(f"Calendar:    {mc.backend_note()}")
    print(f"Fetching {args.years}yr daily bars ({args.provider}) …")
    frames = _fetch(all_symbols(), args.years, args.refresh, args.provider)
    if not frames:
        print("No data — cannot validate.")
        return 2

    rows = []
    if args.sleeve in ("trend", "all"):
        rows += validate_trend(frames, args.cost_pct)
    if args.sleeve in ("dual", "all"):
        rows += validate_dual(frames, args.cost_pct)
    if args.sleeve in ("rev", "all"):
        rows += validate_rev(frames, args.cost_pct)
    if not rows:
        print("No sleeve produced a result.")
        return 2
    return report(rows)


if __name__ == "__main__":
    sys.exit(main())
