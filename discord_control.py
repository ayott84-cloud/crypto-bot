"""R3 — Discord two-way control plane (Phase O, operator decision).

A droplet-side daemon that polls ONE private Discord channel via a bot
token and answers an allowlisted operator's commands:

  read (instant):     !status  !pnl  !positions  !help
  write (confirmed):  !pause <bot>   !resume <bot>

Security model:
  - Only user IDs in DISCORD_OPERATOR_IDS are heard at all.
  - Write commands do nothing until the SAME user replies with the
    confirmation word within the window (default 120s).
  - Writes go through control_flags.json, which kill_switch.should_pause
    consults on every entry check — no sudo, no restarts, effective
    within one bot poll cycle, with a who/when audit trail.
  - v1 deliberately has NO commands that place, modify, or exit trades.

.env (droplet):
  DISCORD_BOT_TOKEN=...            # bot token, NOT the webhook
  DISCORD_CONTROL_CHANNEL_ID=...   # the private channel's snowflake ID
  DISCORD_OPERATOR_IDS=1234,5678   # comma-separated user snowflakes
  DISCORD_CONFIRM_WORD=CONFIRM     # optional override

Run: venv/bin/python discord_control.py
Installed by: deploy/crypto-discord-control.service
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

import requests

BOT_DIR = Path(__file__).resolve().parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

logger = logging.getLogger("crypto_bot.discord_control")

_API = "https://discord.com/api/v10"
_STATE_PATH = BOT_DIR / ".discord_control_state.json"
POLL_INTERVAL_SECONDS = 5

_HELP = (
    "commands: !status · !pnl · !positions · !help · "
    "!pause <bot> · !resume <bot>  (write commands ask for a "
    "confirmation word). bots: momentum whale funding scalp crossover "
    "breakout pair reversal")


class ControlSession:
    """Command parser + confirmation state machine (pure; no HTTP)."""

    def __init__(self, operator_ids: set, confirm_word: str = "CONFIRM",
                  confirm_window_s: int = 120):
        self.operator_ids = {str(x) for x in operator_ids}
        self.confirm_word = confirm_word
        self.confirm_window_s = confirm_window_s
        self._pending: dict = {}     # user_id -> (action, owner, asked_at)

    # ── read-command text builders (real data; monkeypatched in tests) ──
    def _status_text(self) -> str:
        import kill_switch as ks
        parts = []
        try:
            for owner, s in ks.status_summary().items():
                mark = "🛑" if s.get("paused") else "✅"
                parts.append(f"{mark} {owner}"
                              + (f" — {s['reason'][:60]}" if s.get("paused") else ""))
        except Exception as e:  # noqa: BLE001
            parts.append(f"kill-switch read failed: {type(e).__name__}")
        try:
            stale = [p.name for p in sorted(BOT_DIR.glob(".*_heartbeat"))
                      if time.time() - p.stat().st_mtime > 1800]
            parts.append("heartbeats: " + ("all fresh" if not stale
                          else "STALE " + ", ".join(stale)))
        except Exception:  # noqa: BLE001
            pass
        return "\n".join(parts)

    def _pnl_text(self) -> str:
        from datetime import datetime, timedelta
        from collections import defaultdict
        from journal import read_trades
        try:
            trades = read_trades(max_rows=2000)
        except Exception as e:  # noqa: BLE001
            return f"journal read failed: {type(e).__name__}"
        out = []
        for label, hours in (("24h", 24), ("7d", 168)):
            cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
            by_bot = defaultdict(float)
            for t in trades:
                if (t.get("result") in ("WIN", "LOSS")
                        and (t.get("date_closed") or "") >= cutoff):
                    by_bot[t.get("bot") or "?"] += float(t.get("net_pnl") or 0)
            total = sum(by_bot.values())
            detail = " ".join(f"{b} {v:+.2f}" for b, v in sorted(by_bot.items()))
            out.append(f"{label}: {total:+.2f}  ({detail or 'no closes'})")
        return "\n".join(out)

    def _positions_text(self) -> str:
        from position_manager import load_state
        try:
            positions = load_state().get("positions", {}) or {}
        except Exception as e:  # noqa: BLE001
            return f"state read failed: {type(e).__name__}"
        if not positions:
            return "flat — no open positions"
        lines = []
        for key, pos in sorted(positions.items()):
            sl = pos.get("sl_price")
            tp = pos.get("tp_price")
            lines.append(f"{key}: {pos.get('direction', '?')} @ "
                          f"{pos.get('entry_price', '?')} "
                          f"SL {sl if sl else '—'} TP {tp if tp else '—'}")
        return "\n".join(lines)

    # ── the state machine ────────────────────────────────────────────────
    def handle(self, user_id: str, content: str, now: float):
        """Returns the reply text, or None when the message is ignored."""
        if str(user_id) not in self.operator_ids:
            return None
        text = (content or "").strip()

        # Confirmation for a pending write?
        if text == self.confirm_word:
            pending = self._pending.pop(str(user_id), None)
            if not pending:
                return "nothing pending to confirm"
            action, owner, asked_at = pending
            if now - asked_at > self.confirm_window_s:
                return (f"confirmation expired ({int(now - asked_at)}s > "
                         f"{self.confirm_window_s}s) — send the command again")
            from control_flags import set_flag
            set_flag(owner, paused=(action == "pause"),
                      by=f"discord:{user_id}")
            state = "paused" if action == "pause" else "resumed"
            return (f"{owner} {state} ✅ (takes effect on its next entry "
                     "check; exits keep managing)")

        low = text.lower()
        if not low.startswith("!"):
            return None
        if low == "!help":
            return _HELP
        if low == "!status":
            return self._status_text()
        if low == "!pnl":
            return self._pnl_text()
        if low == "!positions":
            return self._positions_text()

        for action in ("pause", "resume"):
            if low.startswith(f"!{action} "):
                from kill_switch import _RECOGNIZED_OWNERS
                owner = low.split(None, 1)[1].strip()
                if owner not in _RECOGNIZED_OWNERS:
                    return (f"unknown bot '{owner}' — one of: "
                             + " ".join(_RECOGNIZED_OWNERS))
                self._pending[str(user_id)] = (action, owner, now)
                return (f"about to {action.upper()} {owner} — reply "
                         f"{self.confirm_word} within "
                         f"{self.confirm_window_s}s to execute")
        return "unrecognized command — " + _HELP


# ─── HTTP shell (thin; all logic lives in ControlSession) ─────────────────

def _load_last_id():
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8")).get("last_id")
    except Exception:  # noqa: BLE001
        return None


def _save_last_id(message_id: str) -> None:
    _STATE_PATH.write_text(json.dumps({"last_id": message_id}),
                             encoding="utf-8")


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    token = os.getenv("DISCORD_BOT_TOKEN", "")
    channel = os.getenv("DISCORD_CONTROL_CHANNEL_ID", "")
    operators = {s.strip() for s in
                  os.getenv("DISCORD_OPERATOR_IDS", "").split(",") if s.strip()}
    if not token or not channel or not operators:
        logger.error("DISCORD_BOT_TOKEN / DISCORD_CONTROL_CHANNEL_ID / "
                      "DISCORD_OPERATOR_IDS missing from .env — exiting")
        sys.exit(1)

    session = ControlSession(
        operator_ids=operators,
        confirm_word=os.getenv("DISCORD_CONFIRM_WORD", "CONFIRM"))
    headers = {"Authorization": f"Bot {token}",
                "User-Agent": "crypto-bot-control/1.0"}
    last_id = _load_last_id()
    logger.info("Discord control plane up — channel %s, %d operator(s)",
                 channel, len(operators))

    _last_stamp = 0.0
    while True:
        # Liveness stamp for the dashboard Routines panel, throttled so
        # the 5s poll loop doesn't hammer the disk.
        if time.time() - _last_stamp > 60:
            from routine_stamps import stamp
            stamp("discord_control")
            _last_stamp = time.time()
        try:
            params = {"limit": 20}
            if last_id:
                params["after"] = last_id
            r = requests.get(f"{_API}/channels/{channel}/messages",
                              headers=headers, params=params, timeout=15)
            if r.status_code == 429:
                wait = float(r.json().get("retry_after", 5))
                logger.warning("rate limited %.1fs", wait)
                time.sleep(wait)
                continue
            if r.status_code != 200:
                logger.warning("poll HTTP %d: %s", r.status_code, r.text[:150])
                time.sleep(POLL_INTERVAL_SECONDS * 4)
                continue
            messages = sorted(r.json(), key=lambda m: int(m["id"]))
            for m in messages:
                last_id = m["id"]
                if m.get("author", {}).get("bot"):
                    continue
                reply = session.handle(m.get("author", {}).get("id", ""),
                                        m.get("content", ""), now=time.time())
                if reply:
                    requests.post(f"{_API}/channels/{channel}/messages",
                                   headers=headers,
                                   json={"content": reply[:1900]}, timeout=15)
            if messages:
                _save_last_id(last_id)
        except requests.RequestException as e:
            logger.warning("poll failed: %s", e)
        except Exception as e:  # noqa: BLE001
            logger.error("control loop error: %s", e, exc_info=True)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()
