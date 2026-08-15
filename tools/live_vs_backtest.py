"""Is a bot's live result distinguishable from variance around its own
validated win rate?

Aug 14 2026. Breakout showed n=10, WR 10%, PF 0.12, -$26.01 over 30 days
and ETHUSDT went 0-for-5. The reflex is to park the asset. But ETH_4H
carries the strongest honest evidence in the fleet — PF 1.87 across 107
trades and 7.8 years on the as-of harness — and a 42%-win-rate strategy
loses five in a row about 6.5% of the time. That is an ordinary streak,
not a broken strategy, and parking on it would be exactly the
small-sample reaction the project keeps guarding against. Scalp was
parked on a 2.85-YEAR window, not a five-trade one.

Three verdicts, and the third is the one that matters most:

  CONSISTENT  — live is within variance of the validated rate
  DIVERGENT   — live is worse than chance can comfortably explain
  UNDERPOWERED— even ZERO wins in this many trades could not clear the
                bar, so the sample cannot inform the question at all

UNDERPOWERED is deliberately NOT folded into CONSISTENT. They read the
same on a dashboard and mean opposite things: one is evidence the
strategy is fine, the other is an absence of evidence either way. At
n=5 against a 42% rate, the worst possible outcome still lands at 6.5%
— nothing observable in that window could have rejected the strategy.

The test is one-sided on purpose. Outperformance needs no defence.

  venv/bin/python tools/live_vs_backtest.py --days 30
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from math import ceil, comb, log
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

ALPHA = 0.05


def binomial_tail_le(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p) — the chance of doing THIS badly
    or worse if the validated win rate still holds."""
    if n <= 0:
        return 1.0
    p = min(max(float(p), 0.0), 1.0)
    k = min(max(int(k), 0), int(n))
    return sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def min_trades_to_reject(p: float, alpha: float = ALPHA) -> int:
    """Smallest n at which even zero wins would clear the bar.

    Below it, no observable outcome can reject the strategy, so the
    window is not a test — it is a wait.
    """
    if p <= 0.0:
        return 0
    if p >= 1.0:
        return 1
    # Solve (1 - p)^n <= alpha for the smallest integer n. Both logs are
    # negative, so the division is positive and ceil is the answer.
    return max(1, ceil(log(alpha) / log(1.0 - p)))


def verdict(wins: int, n: int, expected_wr_pct: float,
              alpha: float = ALPHA) -> dict:
    """Compare a live win count against the validated win rate."""
    p = float(expected_wr_pct) / 100.0
    if n <= 0:
        return {"verdict": "NO DATA", "n": 0, "p_value": None,
                "min_n": min_trades_to_reject(p, alpha)}
    pv = binomial_tail_le(wins, n, p)
    floor = binomial_tail_le(0, n, p)      # best case the test could do
    if floor > alpha:
        label = "UNDERPOWERED"
    elif pv <= alpha:
        label = "DIVERGENT"
    else:
        label = "CONSISTENT"
    return {"verdict": label, "n": n, "wins": wins, "p_value": pv,
            "expected_wr": expected_wr_pct,
            "live_wr": wins / n * 100.0,
            "min_n": min_trades_to_reject(p, alpha)}


def _stats_tables() -> dict:
    """{strategy_name: validated_stats} across every bot that has them.

    Candidate and demoted asset dicts are included deliberately: an
    asset that was demoted still trades out its open positions, and its
    rows land in the window. Skipping them reported "no validated win
    rate on file" for a strategy whose evidence is the very reason it
    was demoted.
    """
    out = {}
    sources = (
        ("breakout_config", ("BREAKOUT_ASSETS", "BREAKOUT_CANDIDATE_ASSETS"),
          "BREAKOUT_BACKTEST_STATS"),
        ("scalp_config",    ("SCALP_ASSETS", "SCALP_CANDIDATE_ASSETS"),
          "SCALP_BACKTEST_STATS"),
        ("crossover_config", ("CROSSOVER_ASSETS",), "CROSSOVER_BACKTEST_STATS"),
        ("stock_config",    ("STOCK_REV_ASSETS", "STOCK_TREND_ASSETS"),
          "STOCK_BACKTEST_STATS"),
    )
    for module, asset_names, stats_name in sources:
        try:
            mod = __import__(module)
            stats = getattr(mod, stats_name, {}) or {}
            for asset_name in asset_names:
                for key, cfg in (getattr(mod, asset_name, {}) or {}).items():
                    st = stats.get(key)
                    if st:
                        out.setdefault(cfg.get("strategy_name", key), st)
        except Exception:  # noqa: BLE001
            continue
    try:
        from config import ASSETS
        for key, cfg in ASSETS.items():
            st = cfg.get("backtest_stats")
            if st:
                out.setdefault(cfg.get("strategy_name", key), st)
    except Exception:  # noqa: BLE001
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    from journal import read_trades
    cutoff = (datetime.now() - timedelta(days=args.days)).isoformat()
    by_strategy = defaultdict(list)
    for t in read_trades(max_rows=10000):
        if t.get("result") not in ("WIN", "LOSS"):
            continue
        when = t.get("date_closed") or t.get("date_opened") or ""
        if when >= cutoff:
            by_strategy[t.get("strategy") or "?"].append(t)

    tables = _stats_tables()
    print(f"=== LIVE vs VALIDATED — last {args.days}d ===\n")
    if not by_strategy:
        print("  no closed trades in window")
        return 0

    for name in sorted(by_strategy):
        rows = by_strategy[name]
        wins = sum(1 for t in rows if t.get("result") == "WIN")
        st = tables.get(name)
        if not st:
            print(f"  {name:24s} n={len(rows):3d} wins={wins:3d}  "
                   f"-> no validated stats on file")
            continue
        if not st.get("wr"):
            # Distinct from "no stats": StockRev's 25-year S2 run recorded
            # PF, drawdown and DSR but never a win rate, so the gap is in
            # the validation record, not in this tool.
            print(f"  {name:24s} n={len(rows):3d} wins={wins:3d}  "
                   f"-> stats on file (PF {st.get('pf', '—')}) but NO win "
                   f"rate recorded — cannot compare")
            continue
        v = verdict(wins, len(rows), float(st["wr"]))
        pv = f"{v['p_value']:.3f}" if v["p_value"] is not None else "—"
        print(f"  {name:24s} n={v['n']:3d} live {v['live_wr']:5.1f}% vs "
               f"validated {v['expected_wr']:5.1f}%  p={pv}  -> {v['verdict']}")
        if v["verdict"] == "UNDERPOWERED":
            print(f"  {'':24s}    needs n>={v['min_n']} before ANY outcome "
                   f"could reject it")
    print("\nOne-sided: only underperformance is tested. UNDERPOWERED is "
           "not a pass — it means the window cannot answer the question.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
