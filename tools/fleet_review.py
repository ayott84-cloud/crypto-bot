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

    # 2. Per-asset for the live bots
    for bot in ("Scalp", "Momentum", "Breakout", "Funding"):
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
    except Exception as e:  # noqa: BLE001
        print(f"\n-- Open positions -- unavailable: {e}")

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
