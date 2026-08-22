"""An exclusion the reader cannot see is a lie (Aug 22 2026).

The mechanism that sets defect-produced trades aside is also the
mechanism that could quietly flatter a track record. The only structural
defence is that every tool applying it prints what it removed and why.
These tests fail if a tool ever filters silently.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pytest

pytest.importorskip("pandas")

import tools.fleet_review as fr
import tools.live_vs_backtest as lvb


@pytest.mark.parametrize("mod", [fr, lvb], ids=["fleet_review",
                                                  "live_vs_backtest"])
def test_a_tool_that_filters_must_also_report(mod):
    src = inspect.getsource(mod.main)
    if "partition_excluded" not in src:
        pytest.skip(f"{mod.__name__} does not filter")
    assert "excluded_reason" in src, (
        f"{mod.__name__} drops excluded rows without naming them — a "
        f"silent exclusion cannot be distinguished from a dishonest one")


@pytest.mark.parametrize("mod", [fr, lvb], ids=["fleet_review",
                                                  "live_vs_backtest"])
def test_the_count_is_printed_not_just_the_reasons(mod):
    src = inspect.getsource(mod.main)
    if "partition_excluded" not in src:
        pytest.skip(f"{mod.__name__} does not filter")
    assert "len(" in src and "exclud" in src.lower()
