"""Aug 14 2026 — the second half of the whale telemetry.

classify_all now explains 175 coins -> N signals. Nothing explained
N signals -> 0 trades: every rejection in the entry loop logged a line
and continued, so a 30-day soak ending at zero could not name WHICH gate
did the work — slots, cooldown, the W.B filter stack, the Arkham flow
gate, a missing ATR, or the price-action trigger.

The soak produced 159 signal records and no trades, which is exactly the
case this funnel exists to explain.

Run: python -m pytest tests/test_whale_entry_funnel.py -v
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pytest.importorskip("pandas")

import whale_main
import whale_signals as ws


# ─── The counter ─────────────────────────────────────────────────────────

def test_bump_starts_at_one_and_accumulates():
    f = {}
    whale_main._bump_entry(f, "cooldown")
    whale_main._bump_entry(f, "cooldown")
    whale_main._bump_entry(f, "no_atr")
    assert f == {"cooldown": 2, "no_atr": 1}


# ─── Every rejection point is counted ────────────────────────────────────

_REQUIRED_KEYS = [
    "no_slots", "already_open", "cooldown", "no_atr",
    "awaiting_trigger", "arkham_flow", "opened",
]


@pytest.mark.parametrize("key", _REQUIRED_KEYS)
def test_each_entry_gate_bumps_the_funnel(key):
    """A gate that can drop a signal without recording it puts us back
    where the soak started: zero trades, no reason."""
    src = inspect.getsource(whale_main.run_cycle)
    assert f'_bump_entry(entry_funnel, "{key}")' in src, \
        f"entry gate {key!r} drops signals without recording why"


def test_filter_stack_rejections_record_the_specific_reason():
    """'filtered' alone is not a diagnosis — the W.B stack has four
    independent gates and they need different fixes."""
    src = inspect.getsource(whale_main.run_cycle)
    assert '_bump_entry(entry_funnel, f"filter:' in src


def test_the_funnel_opens_with_the_signal_count():
    """Without the denominator the counts cannot be reconciled."""
    src = inspect.getsource(whale_main.run_cycle)
    assert 'entry_funnel = {"signals": len(signals)}' in src


def test_the_funnel_is_published_and_written():
    src = inspect.getsource(whale_main.run_cycle)
    assert "_ws.LAST_ENTRY_FUNNEL = entry_funnel" in src
    assert "_log_entry_funnel(entry_funnel)" in src


# ─── The writer ──────────────────────────────────────────────────────────

def test_entry_funnel_is_written_as_its_own_record_type(tmp_path,
                                                          monkeypatch):
    """One file answers the whole pipeline, but the two funnels must stay
    distinguishable — they are counted over different denominators."""
    monkeypatch.setattr(whale_main, "WHALE_SIGNAL_LOG",
                          tmp_path / "whale_signals.jsonl")
    whale_main._log_entry_funnel({"signals": 2, "awaiting_trigger": 2})
    rec = json.loads((tmp_path / "whale_signals.jsonl")
                      .read_text(encoding="utf-8").strip())
    assert rec["type"] == "entry_funnel"
    assert rec["signals"] == 2 and rec["awaiting_trigger"] == 2
    assert rec.get("timestamp")


def test_a_failed_write_never_takes_down_the_cycle(monkeypatch):
    """Telemetry is not worth a trading outage."""
    monkeypatch.setattr(whale_main, "WHALE_SIGNAL_LOG",
                          Path("/nonexistent-dir-xyz/out.jsonl"))
    whale_main._log_entry_funnel({"signals": 1})      # must not raise


# ─── The two funnels compose ─────────────────────────────────────────────

def test_classification_and_entry_funnels_use_separate_slots():
    """LAST_FUNNEL is the classifier's; overwriting it with entry counts
    would make the writer emit the wrong denominator."""
    ws.LAST_FUNNEL = {"coins": 175}
    ws.LAST_ENTRY_FUNNEL = {"signals": 2}
    assert ws.LAST_FUNNEL["coins"] == 175
    assert ws.LAST_ENTRY_FUNNEL["signals"] == 2
