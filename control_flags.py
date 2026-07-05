"""R3 — operator control flags (Discord control plane's write path).

A tiny JSON file the Discord daemon writes and kill_switch.should_pause
reads on every entry check. This is deliberately NOT the .env pause
flags: those need service restarts; these take effect within one poll
cycle and carry an audit trail (who, when).

Fail-open on any read problem — a corrupt flags file must never freeze
the fleet.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_FLAGS_PATH = Path(__file__).resolve().parent / "control_flags.json"


def get_flags() -> dict:
    try:
        return json.loads(_FLAGS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing/corrupt file = no flags
        return {}


def is_operator_paused(owner: str) -> bool:
    entry = get_flags().get(owner) or {}
    return bool(entry.get("paused"))


def set_flag(owner: str, paused: bool, by: str) -> None:
    flags = get_flags()
    flags[owner] = {
        "paused": bool(paused),
        "by":      by,
        "at":      datetime.now(timezone.utc).isoformat(),
    }
    # Atomic replace so a bot reading mid-write never sees a torn file
    fd, tmp = tempfile.mkstemp(dir=str(_FLAGS_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(flags, fh, indent=2)
        os.replace(tmp, _FLAGS_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
