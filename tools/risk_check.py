"""R2 — hourly risk sentinel (Phase O, Jul 2026).

Checks, in order of severity:
  1. Kill-switch state per owner + the 24h drawdown vs the effective
     breaker (kill_switch._daily_dd_threshold_usd — the tighter of the
     fixed floor and 3% of capital).
  2. Heartbeat staleness — any .*_heartbeat file older than 30 min means
     a bot process is wedged or dead while systemd thinks it's fine.
  3. Open positions missing a persisted SL where the P5a marker fields
     say one should exist (bracket_kind / exit_kind present).

Default mode is ALERT-ONLY: a Discord embed goes out only when something
is wrong, so the hourly timer stays silent on healthy days. --always
sends a heartbeat summary regardless.

Exit code is ALWAYS 0 — a flapping oneshot would spam systemd state.

Run (droplet): venv/bin/python tools/risk_check.py [--always]
Installed by: deploy/crypto-risk-check.service + .timer
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

STALE_AFTER_SECONDS = 30 * 60


def _parked_owners() -> set:
    """Bots at revalidation step 0 (PARKED) — their services may be
    disabled outright, so a relic heartbeat file is expected, not an
    incident (the .reversal_heartbeat hourly false positive, Jul 5-6).
    Unreadable status file → empty set (fail toward alerting)."""
    import json
    try:
        raw = json.loads((BOT_DIR / "revalidation_status.json")
                          .read_text(encoding="utf-8"))
        return {bot for bot, info in raw.items()
                 if int(info.get("step", 1)) == 0}
    except Exception:  # noqa: BLE001
        return set()


def classify_heartbeats(paths, stale_after_s: int = STALE_AFTER_SECONDS,
                          now_ts: float | None = None) -> list:
    """[{name, age_s, stale}] for each heartbeat file that exists.

    PARKED bots have no duty to beat — a STALE parked heartbeat is a
    relic of a stopped service and stays silent (the .reversal_heartbeat
    false positive, Jul 5-6). But a FRESH parked heartbeat means the
    service is STILL RUNNING while the fleet believes it parked — that's
    how the pair bot traded 216 legs in 14 days invisibly (Jul 16). Those
    rows get parked_alive=True so build_issues can alarm."""
    now = time.time() if now_ts is None else now_ts
    parked = _parked_owners()
    out = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        # ".reversal_heartbeat" → owner "reversal"
        owner = p.name.lstrip(".").removesuffix("_heartbeat")
        age = now - p.stat().st_mtime
        if owner in parked:
            if age > stale_after_s:
                continue  # relic of a stopped service — expected
            out.append({"name": p.name, "age_s": int(age),
                         "stale": False, "parked_alive": True})
            continue
        out.append({"name": p.name, "age_s": int(age),
                     "stale": age > stale_after_s})
    return out


def positions_missing_sl(positions: dict) -> list:
    """State keys whose position SHOULD carry a persisted sl_price
    (post-P5a marker fields present) but doesn't. Legacy positions
    without marker fields are skipped — absence of evidence."""
    flagged = []
    for key, pos in (positions or {}).items():
        has_marker = pos.get("bracket_kind") or pos.get("exit_kind")
        if has_marker and not pos.get("sl_price"):
            flagged.append(key)
    return flagged


def build_issues(ks_summary: dict, daily: dict, heartbeats: list,
                  missing_sl: list) -> list:
    """Human-readable issue lines. Empty list = healthy."""
    issues = []
    if daily.get("breached"):
        issues.append(
            f"BREAKER TRIPPED: 24h PnL ${daily['pnl']:+,.2f} <= "
            f"${daily['threshold']:,.2f} — all new entries blocked")
    for owner, s in (ks_summary or {}).items():
        if s.get("paused") and not daily.get("breached"):
            # per-owner trips only worth a line when not already covered
            # by the account-wide breaker message
            issues.append(f"kill-switch tripped for {owner}: {s.get('reason', '')}")
    for hb in heartbeats:
        if hb.get("parked_alive"):
            issues.append(
                f"{hb['name']} is beating but its bot is PARKED — the "
                "service is still running and may still be trading; "
                "stop + disable it")
        elif hb["stale"]:
            issues.append(
                f"stale heartbeat {hb['name']} — {hb['age_s'] // 60} min old "
                "(bot wedged while service shows active?)")
    for key in missing_sl:
        issues.append(f"open position {key} has NO persisted SL — "
                       "exchange bracket may be missing")
    return issues


def main() -> int:
    always = "--always" in sys.argv

    import kill_switch as ks
    from journal import read_trades
    from position_manager import load_state
    from notifier import _send_discord_embed, _DISCORD_RED, _DISCORD_GREEN

    # 1. Kill switch + daily drawdown
    try:
        summary = ks.status_summary()
        trades = read_trades(max_rows=1000)
        closed = [t for t in trades if t.get("result") in ("WIN", "LOSS")]
        pnl = ks._trailing_pnl(closed, hours=24)
        thr = ks._daily_dd_threshold_usd()
        daily = {"pnl": pnl, "threshold": thr, "breached": pnl <= thr}
    except Exception as e:  # noqa: BLE001
        summary, daily = {}, {"pnl": 0.0, "threshold": 0.0, "breached": False}
        print(f"WARN: kill-switch/journal read failed: {e}")

    # 2. Heartbeats
    heartbeats = classify_heartbeats(sorted(BOT_DIR.glob(".*_heartbeat")))

    # 3. Missing SLs
    try:
        state = load_state()
        missing = positions_missing_sl(state.get("positions", {}))
    except Exception as e:  # noqa: BLE001
        missing = []
        print(f"WARN: state read failed: {e}")

    issues = build_issues(summary, daily, heartbeats, missing)

    for line in issues:
        print("ISSUE:", line)
    if not issues:
        print(f"healthy — 24h PnL ${daily['pnl']:+,.2f} vs "
               f"${daily['threshold']:,.2f}, "
               f"{len(heartbeats)} heartbeats fresh")

    if issues or always:
        title = ("🛑 Risk check — ISSUES" if issues
                  else "✅ Risk check — healthy")
        fields = [{"name": "24h PnL vs breaker",
                    "value": f"${daily['pnl']:+,.2f} / ${daily['threshold']:,.2f}",
                    "inline": True},
                   {"name": "Heartbeats",
                    "value": f"{sum(1 for h in heartbeats if not h['stale'])}"
                              f"/{len(heartbeats)} fresh", "inline": True}]
        desc = "\n".join(f"• {i}" for i in issues) if issues else "All checks passed."
        _send_discord_embed(title=title, description=desc[:3900],
                              color=_DISCORD_RED if issues else _DISCORD_GREEN,
                              fields=fields)

    from routine_stamps import stamp
    stamp("risk_check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
