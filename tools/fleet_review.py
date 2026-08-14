"""One-command periodic fleet review (Phase O day-N review, tool form).

Prints everything the operator used to gather via paste-command blocks:
per-bot stats, per-asset breakdowns for the live sets, ETH scalp's
Step-4 gate verdict, exit-reason distribution vs the runbook
thresholds, kill-switch/breaker state, routine liveness, heartbeats,
prediction-scanner accumulation, and open positions with brackets.

Run (droplet): venv/bin/python tools/fleet_review.py [--days 14]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))


# ─── Pure aggregation helpers ──────────────────────────────────────────────

def _closed_in_window(trades: list, days: int) -> list:
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    return [t for t in trades or []
            if t.get("result") in ("WIN", "LOSS")
            and (t.get("date_closed") or "") >= cutoff]


def bot_stats(trades: list, days: int = 14) -> dict:
    """{bot: {n, wins, losses, wr, pf, net, best, worst}}"""
    out = {}
    by_bot = defaultdict(list)
    for t in _closed_in_window(trades, days):
        by_bot[t.get("bot") or "?"].append(float(t.get("net_pnl") or 0))
    for bot, pnls in by_bot.items():
        gw = sum(p for p in pnls if p > 0)
        gl = abs(sum(p for p in pnls if p < 0))
        out[bot] = {
            "n":     len(pnls),
            "wins":  sum(1 for p in pnls if p > 0),
            "losses": sum(1 for p in pnls if p < 0),
            "wr":    round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1),
            "pf":    (round(gw / gl, 2) if gl > 0 else (999.0 if gw > 0 else 0.0)),
            "net":   round(sum(pnls), 2),
            "best":  round(max(pnls), 2),
            "worst": round(min(pnls), 2),
        }
    return out


def symbol_stats(trades: list, bot: str, days: int = 14) -> list:
    by_sym = defaultdict(list)
    for t in _closed_in_window(trades, days):
        if (t.get("bot") or "") != bot:
            continue
        by_sym[t.get("symbol") or "?"].append(float(t.get("net_pnl") or 0))
    rows = []
    for sym, pnls in sorted(by_sym.items()):
        gw = sum(p for p in pnls if p > 0)
        gl = abs(sum(p for p in pnls if p < 0))
        rows.append({"symbol": sym, "n": len(pnls),
                      "wr": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1),
                      "pf": (round(gw / gl, 2) if gl > 0 else (999.0 if gw > 0 else 0.0)),
                      "net": round(sum(pnls), 2)})
    return rows


def merged_signal_status(state: dict) -> dict:
    """Every bot's per-asset signal_status, across owner namespaces.

    position_manager._TOPLEVEL_BY_BOT namespaces top-level state keys per
    owner, so Module 2's daemon writes "stock_signal_status" rather than
    clobbering the crypto fleet's "signal_status" on merge. That is the
    right call on the write side — but the Entry-blockers panel read only
    the crypto key, so from Aug 2 (unpause) to Aug 14 the review could
    not say whether the equity sleeves were evaluating at all.

    Non-dict values are skipped rather than raised on: a corrupt state
    file should degrade this panel, not the whole review.
    """
    out: dict = {}
    for key in ("signal_status", "stock_signal_status"):
        section = (state or {}).get(key)
        if isinstance(section, dict):
            out.update(section)
    return out


def blocked_by_rows(signal_status: dict, max_age_h: float = 24.0,
                      now=None) -> list:
    """Group per-asset signal_status into [{reason, n, assets}] rows,
    sorted by count desc. Entries checked more than max_age_h ago are
    relics (parked bots' assets) and dropped. would_enter=True groups
    under WOULD_ENTER — an asset that WANTS to trade but hasn't fired
    an entry is a different conversation than a blocked one."""
    now = now or datetime.now(timezone.utc)
    groups: dict = defaultdict(list)
    for asset, sig in (signal_status or {}).items():
        if not isinstance(sig, dict):
            continue
        try:
            checked = datetime.fromisoformat(sig.get("checked_at") or "")
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=timezone.utc)
            age_h = (now - checked).total_seconds() / 3600.0
        except ValueError:
            continue
        if age_h > max_age_h:
            continue
        reason = ("WOULD_ENTER" if sig.get("would_enter")
                   else (sig.get("blocked_by") or "?"))
        groups[reason].append(asset)
    return sorted(
        ({"reason": r, "n": len(a), "assets": sorted(a)}
          for r, a in groups.items()),
        key=lambda row: -row["n"])


def symbol_exposure(positions: dict) -> list:
    """Cross-bot open exposure per symbol (fleet-audit W5 step 7:
    bots holding the same asset are one bot with extra failure modes).
    [{symbol, n, multi_bot, holders}] sorted by n desc."""
    by_sym: dict = defaultdict(list)
    for key, pos in (positions or {}).items():
        sym = (pos or {}).get("symbol") or "?"
        by_sym[sym].append(f"{key} {(pos or {}).get('direction', '?')}")
    return sorted(
        ({"symbol": s, "n": len(h), "multi_bot": len(h) > 1,
           "holders": sorted(h)}
          for s, h in by_sym.items()),
        key=lambda r: -r["n"])


# Module 2 made the fleet multi-asset-class, and "is it alpha or beta"
# only means something against the RIGHT beta. Comparing a stock sleeve
# to BTC would be noise dressed as a benchmark.
_STOCK_BOTS = {"StockTrend", "StockDual", "StockRev"}
_BENCHMARKS = {
    "crypto": {"symbol": "BTCUSDT", "interval": "1d", "source": "binance",
                "label": "BTC buy-hold"},
    "equity": {"symbol": "SPY", "interval": "1d", "source": "equity",
                "label": "SPY buy-hold"},
}


def benchmark_for_bots(bots, all_classes: bool = False):
    """Pick the benchmark(s) matching the bots that actually traded.

    all_classes=True returns every benchmark the window touched — once
    two modules are live, a single number cannot describe the fleet.
    """
    classes = []
    if any(b in _STOCK_BOTS for b in (bots or set())):
        classes.append("equity")
    if any(b not in _STOCK_BOTS for b in (bots or set())):
        classes.append("crypto")
    if not classes:
        classes = ["crypto"]
    if all_classes:
        return [_BENCHMARKS[c] for c in classes]
    return _BENCHMARKS[classes[0]]


def btc_benchmark(closes) -> dict | None:
    """Buy-and-hold BTC return over the window (fleet-audit W4 step 3:
    is the alpha real or is it beta). None when insufficient data."""
    closes = [float(c) for c in (closes or [])]
    if len(closes) < 2 or closes[0] == 0:
        return None
    return {"pct": (closes[-1] / closes[0] - 1.0) * 100.0}


def step4_verdict(pf, n: int) -> dict:
    """The Step-4 paper-window gate: PF >= 1.3 over >= 10 closed trades."""
    if pf is None or n == 0:
        return {"verdict": "NO TRADES"}
    if n < 10:
        return {"verdict": "HOLD (n<10)"}
    if pf >= 1.3:
        return {"verdict": "PASS"}
    if pf >= 1.0:
        return {"verdict": "HOLD (PF<1.3)"}
    return {"verdict": "FAIL (PF<1.0)"}


# ─── Report ────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()
    days = args.days

    from journal import read_trades
    trades = read_trades(max_rows=5000)

    print(f"=== FLEET REVIEW — last {days}d — "
           f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ===")

    # 1. Per-bot
    print(f"\n-- Per-bot ({days}d closed) --")
    stats = bot_stats(trades, days)
    if not stats:
        print("  no closed trades in window")
    for bot in sorted(stats):
        s = stats[bot]
        print(f"  {bot:10s} n={s['n']:3d}  WR={s['wr']:5.1f}%  "
               f"PF={s['pf']:5.2f}  net={s['net']:+8.2f}  "
               f"best={s['best']:+7.2f}  worst={s['worst']:+7.2f}")

    # 1b. Benchmark honesty — what did simply HOLDING return? Per asset
    # class, because a stock sleeve measured against BTC is noise.
    fleet_net = sum(s["net"] for s in stats.values())
    lines = []
    for bench in benchmark_for_bots(set(stats), all_classes=True):
        try:
            if bench["source"] == "equity":
                from datetime import date as _date, timedelta as _td
                from tools import _equity_bars as _eb
                end = _date.today()
                df = _eb.fetch_daily(bench["symbol"], end - _td(days=days + 5),
                                       end, verify_complete=False)
                closes = [float(c) for c in df["close"]] if len(df) else []
            else:
                from tools._binance_klines import fetch_klines_chained
                closes = [r[4] for r in
                           fetch_klines_chained(bench["symbol"], "1d", days + 1)]
            b = btc_benchmark(closes)
            if b:
                lines.append(f"{bench['label']} {days}d: {b['pct']:+.1f}%")
        except Exception:  # noqa: BLE001
            continue
    if lines:
        print(f"\n-- Benchmark -- " + "  |  ".join(lines)
               + f"  |  fleet net: ${fleet_net:+,.2f} "
               "(dollars on small paper margins — directional read only)")

    # 2. Per-asset for the live bots
    for bot in ("Scalp", "Momentum", "Breakout", "Funding",
                 "StockRev", "StockDual", "StockTrend"):
        rows = symbol_stats(trades, bot, days)
        if rows:
            print(f"\n-- {bot} per asset --")
            for r in rows:
                print(f"  {r['symbol']:10s} n={r['n']:3d}  WR={r['wr']:5.1f}%  "
                       f"PF={r['pf']:5.2f}  net={r['net']:+8.2f}")

    # 3. ETH scalp Step-4 gate
    eth = [t for t in _closed_in_window(trades, days)
            if (t.get("bot") or "") == "Scalp"
            and (t.get("symbol") or "") == "ETHUSDT"]
    pnls = [float(t.get("net_pnl") or 0) for t in eth]
    gw = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    pf = (gw / gl if gl > 0 else (999.0 if gw > 0 else None)) if pnls else None
    v = step4_verdict(pf, len(pnls))
    print(f"\n-- ETH scalp Step-4 gate (PF>=1.3, n>=10) --")
    print(f"  n={len(pnls)}  PF={f'{pf:.2f}' if pf else '—'}  "
           f"net={sum(pnls):+.2f}  ->  {v['verdict']}")

    # 4. Exit reasons vs runbook thresholds
    print(f"\n-- Exit reasons ({days}d) --")
    by_bot_reason = defaultdict(lambda: defaultdict(int))
    for t in _closed_in_window(trades, days):
        by_bot_reason[t.get("bot") or "?"][t.get("exit_reason") or "?"] += 1
    for bot in sorted(by_bot_reason):
        counts = by_bot_reason[bot]
        total = sum(counts.values())
        sl = sum(n for r, n in counts.items()
                  if r in ("SL Hit", "Emergency SL", "BE Hit"))
        tl = sum(n for r, n in counts.items()
                  if r in ("Time Limit", "Time Stop", "Stale Exit"))
        flags = []
        if total and sl / total > 0.6:
            flags.append("SL>60% — brackets too tight?")
        if total and tl / total > 0.4:
            flags.append("time>40% — entries into drift?")
        detail = ", ".join(f"{r}:{n}" for r, n in
                            sorted(counts.items(), key=lambda kv: -kv[1]))
        print(f"  {bot:10s} {detail}" + (f"   ⚠ {'; '.join(flags)}" if flags else ""))

    # 5. Kill switch / breaker
    try:
        import kill_switch as ks
        closed_all = [t for t in trades if t.get("result") in ("WIN", "LOSS")]
        pnl24 = ks._trailing_pnl(closed_all, hours=24)
        thr = ks._daily_dd_threshold_usd()
        tripped = [o for o, s in ks.status_summary().items() if s.get("paused")]
        print(f"\n-- Kill switch --")
        print(f"  24h PnL ${pnl24:+,.2f} vs breaker ${thr:,.2f}  |  "
               f"tripped: {', '.join(tripped) if tripped else 'none'}")
    except Exception as e:  # noqa: BLE001
        print(f"\n-- Kill switch -- unavailable: {e}")

    # 6. Routines + heartbeats
    try:
        from routine_stamps import read_stamps
        now = datetime.now(timezone.utc)
        print("\n-- Routines (last run) --")
        for name, iso in sorted(read_stamps().items()):
            try:
                age = now - datetime.fromisoformat(iso)
                print(f"  {name:18s} {age.total_seconds() / 3600:6.1f}h ago")
            except ValueError:
                print(f"  {name:18s} unparseable")
    except Exception:  # noqa: BLE001
        pass
    try:
        from tools.risk_check import classify_heartbeats
        hbs = classify_heartbeats(sorted(BOT_DIR.glob(".*_heartbeat")))
        stale = [h["name"] for h in hbs if h["stale"]]
        print(f"\n-- Heartbeats -- {len(hbs) - len(stale)}/{len(hbs)} fresh"
               + (f"  STALE: {', '.join(stale)}" if stale else ""))
    except Exception:  # noqa: BLE001
        pass

    # 7. Prediction scanner accumulation
    spreads = BOT_DIR / "prediction_spreads.jsonl"
    if spreads.exists():
        lines = spreads.read_text(encoding="utf-8").strip().splitlines()
        print(f"\n-- Prediction scanner -- {len(lines)} runs logged "
               f"({spreads.stat().st_size // 1024} KB) — run "
               f"tools/prediction_scanner.py --top 5 for current gaps")

    # 8. Open positions with brackets
    try:
        from position_manager import load_state
        positions = load_state().get("positions", {}) or {}
        print(f"\n-- Open positions ({len(positions)}) --")
        for key, pos in sorted(positions.items()):
            sl = pos.get("sl_price")
            tp = pos.get("tp_price")
            print(f"  {key:22s} {pos.get('direction', '?'):5s} "
                   f"@ {pos.get('entry_price', '?')} "
                   f"SL {f'{sl:,.4f}' if sl else '—'} "
                   f"TP {f'{tp:,.4f}' if tp else '—'} "
                   f"phase={pos.get('phase', '—')}")
        exposure = symbol_exposure(positions)
        shared = [r for r in exposure if r["multi_bot"]]
        if shared:
            print("  ⚠ cross-bot same-symbol exposure:")
            for r in shared:
                print(f"    {r['symbol']:10s} x{r['n']}: "
                       + "; ".join(r["holders"]))
    except Exception as e:  # noqa: BLE001
        print(f"\n-- Open positions -- unavailable: {e}")

    # 8.5. Entry blockers — why are quiet bots quiet?
    try:
        from position_manager import load_state as _ls
        rows = blocked_by_rows(merged_signal_status(_ls()))
        print("\n-- Entry blockers (signal_status, checked <24h) --")
        if rows:
            for r in rows:
                names = ", ".join(r["assets"][:6])
                extra = f" +{r['n'] - 6} more" if r["n"] > 6 else ""
                print(f"  {r['reason']:22s} n={r['n']:2d}  {names}{extra}")
        else:
            print("  (none) — no bot recorded a signal check in the last 24h")
    except Exception as e:  # noqa: BLE001
        # This used to `pass`. A swallowed failure here reads exactly like
        # "no bot is blocked", which is the most misleading thing this
        # panel could say.
        print(f"\n-- Entry blockers -- unavailable: {e}")

    # 9. Pipeline stages
    try:
        status = json.loads((BOT_DIR / "revalidation_status.json")
                             .read_text(encoding="utf-8"))
        steps = ["Deploy", "Replay", "X-check", "Shakedown",
                  "Paper 14d", "Micro-live", "Scale"]
        print("\n-- Pipeline --")
        for bot, info in sorted(status.items(),
                                  key=lambda kv: -kv[1].get("step", 0)):
            print(f"  {bot:10s} {steps[min(6, int(info.get('step', 0)))]:10s} "
                   f"{info.get('note', '')[:80]}")
    except Exception:  # noqa: BLE001
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
