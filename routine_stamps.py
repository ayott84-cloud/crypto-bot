"""Run-stamps for the scheduled routines (dashboard Routines panel).

The timers report their real output to Discord/journalctl; this file
exists only so the dashboard can answer "are the routines alive?"
without shelling out to systemctl. Each routine calls stamp(name) at
the end of a successful run; the panel compares stamps to each
routine's expected cadence. Fail-open everywhere — a broken stamps
file must never affect the routines or the dashboard build.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_STAMPS_PATH = Path(__file__).resolve().parent / ".routine_stamps.json"


def read_stamps() -> dict:
    try:
        return json.loads(_STAMPS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def stamp(name: str) -> None:
    try:
        stamps = read_stamps()
        stamps[name] = datetime.now(timezone.utc).isoformat()
        _STAMPS_PATH.write_text(json.dumps(stamps, indent=2),
                                  encoding="utf-8")
    except Exception:  # noqa: BLE001 — never let telemetry break a routine
        pass
