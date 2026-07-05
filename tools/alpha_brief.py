"""R2 — weekday-morning fleet brief (Phase O, Jul 2026).

Composes the operator's 90-second morning read from droplet-local data
only (journal, state, kill switch, revalidation stages) plus BTC/ETH/SOL
prices from the public WEEX kline endpoint. Delivered as Discord embeds
through the existing notifier (webhook stays in .env — never in code).

NO TRADES ARE EXECUTED BY THIS BRIEF — it is read-only by construction:
the only imports with side effects are the notifier send at the end.

Run (droplet): venv/bin/python tools/alpha_brief.py
Installed by: deploy/crypto-alpha-brief.service + .timer
  (Mon-Fri 07:30 America/Chicago)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

_PRICE_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def section_last24(trades: list) -> list:
    """Per-bot 24h closed summary lines."""
    from collections import defaultdict
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    by_bot = defaultdict(list)
    for t in trades or []:
        if (t.get("result") in ("WIN", "LOSS")
                and (t.get("date_closed") or "") >= cutoff):
            by_bot[t.get("bot") or "?"].append(float(t.get("net_pnl") or 0))
    if not by_bot:
        return ["no closed trades in the last 24h"]
    lines = []
    for bot in sorted(by_bot):
        pnls = by_bot[bot]
        wins = sum(1 for p in pnls if p > 0)
        lines.append(f"{bot}: {len(pnls)} closed ({wins}W/"
                      f"{len(pnls) - wins}L) net {sum(pnls):+.2f}")
    return lines


def section_positions(state: dict) -> list:
    positions = (state or {}).get("positions", {}) or {}
    if not positions:
        return ["flat — no open positions"]
    lines = []
    for key, pos in sorted(positions.items()):
        sl = pos.get("sl_price")
        tp = pos.get("tp_price")
        lines.append(
            f"{key}: {pos.get('direction', '?')} @ {pos.get('entry_price', '?')}"
            f" SL {f'{sl:,.4f}' if sl else '—'} TP {f'{tp:,.4f}' if tp else '—'}")
    return lines


def section_gates(status_path: Path) -> list:
    try:
        raw = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ["revalidation_status.json unreadable"]
    steps = ["Deploy", "Replay", "X-check", "Shakedown",
              "Paper 14d", "Micro-live", "Scale"]
    lines = []
    for bot, info in sorted(raw.items(), key=lambda kv: -kv[1].get("step", 0)):
        step = max(0, min(6, int(info.get("step", 0))))
        lines.append(f"{bot}: {steps[step]} — {info.get('note', '')[:70]}")
    return lines


def section_prices(executor) -> list:
    lines = []
    for sym in _PRICE_SYMBOLS:
        try:
            raw = executor.get_klines(sym, "1d", 2)
            prev_close = float(raw[-2][4])
            last = float(raw[-1][4])
            chg = (last - prev_close) / prev_close * 100
            lines.append(f"{sym[:-4]}: {last:,.2f} ({chg:+.2f}% 24h)")
        except Exception as e:  # noqa: BLE001
            lines.append(f"{sym[:-4]}: unavailable ({type(e).__name__})")
    return lines


def chunk_field(lines: list, limit: int = 1024) -> list:
    """Split joined lines into <=limit chunks (Discord field value cap)."""
    chunks, cur = [], ""
    for line in lines:
        piece = line + "\n"
        if len(cur) + len(piece) > limit and cur:
            chunks.append(cur)
            cur = ""
        cur += piece
    if cur:
        chunks.append(cur)
    return chunks


def _send_embed(title, description, color, fields):
    from notifier import _send_discord_embed
    return _send_discord_embed(title=title, description=description,
                                 color=color, fields=fields)


def send_brief(sections: list) -> bool:
    """sections: [(heading, lines)]. Chunks long sections across fields."""
    from notifier import _DISCORD_BLUE
    fields = []
    for heading, lines in sections:
        for n, chunk in enumerate(chunk_field(lines)):
            fields.append({"name": heading if n == 0 else f"{heading} (cont.)",
                            "value": chunk, "inline": False})
    return _send_embed(
        title=f"📋 Alpha Brief · {datetime.now().strftime('%a %b %d')}",
        description="Fleet morning read. "
                     "NO TRADES ARE EXECUTED BY THIS BRIEF.",
        color=_DISCORD_BLUE,
        fields=fields[:25])


def main() -> int:
    from journal import read_trades
    from position_manager import load_state
    from executor import Executor
    import kill_switch as ks

    try:
        trades = read_trades(max_rows=2000)
    except Exception:  # noqa: BLE001
        trades = []
    try:
        state = load_state()
    except Exception:  # noqa: BLE001
        state = {}

    try:
        summary = ks.status_summary()
        tripped = [o for o, s in summary.items() if s.get("paused")]
        ks_line = ("all switches armed" if not tripped
                    else "TRIPPED: " + ", ".join(tripped))
    except Exception as e:  # noqa: BLE001
        ks_line = f"kill-switch state unavailable ({type(e).__name__})"

    sections = [
        ("Kill switches", [ks_line]),
        ("Last 24h",      section_last24(trades)),
        ("Open positions", section_positions(state)),
        ("Markets",        section_prices(Executor())),
        ("Pipeline",       section_gates(BOT_DIR / "revalidation_status.json")),
    ]
    for heading, lines in sections:
        print(f"== {heading} ==")
        for line in lines:
            print("  " + line)

    ok = send_brief(sections)
    print("discord:", "sent" if ok else "not sent (no webhook / disabled)")

    from routine_stamps import stamp
    stamp("alpha_brief")
    return 0


if __name__ == "__main__":
    sys.exit(main())
