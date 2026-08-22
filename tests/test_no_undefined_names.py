"""Static guard against the bug class that cost three weeks (Aug 22 2026).

append_forming_bar called `_dt.date.today()` while `_dt` was imported
only inside _is_rebalance_day's local scope. Every production call
raised NameError; the function's own `except Exception: return df`
swallowed it; and it silently returned an unmodified frame on every
cycle since Module 2 launched. The visible symptom was a "1.83% fill
divergence" that was actually a two-day-stale decision bar.

The test suite was green throughout, because every test passed the
`session=` argument explicitly and never ran the default branch.

pyflakes finds this in milliseconds:

    _pf_probe.py:11:27: undefined name '_dt'

So it runs here, over the whole codebase, on every suite run. A bug that
a linter catches instantly should never again be found three weeks later
by reading a fill price.

This is deliberately narrow — ONLY undefined names. It is not a style
gate and will not fail on unused imports, line length or anything else
subjective. The point is to catch code that cannot run, not code someone
dislikes.

Run: python -m pytest tests/test_no_undefined_names.py -v
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BOT_DIR = Path(__file__).resolve().parent.parent

# A string annotation like Optional["HLContext"] is a forward reference:
# Python never evaluates it, so it cannot raise. pyflakes reports it
# anyway. Each entry needs a reason, so the list stays a record of
# decisions rather than a place to bury real findings.
_ACCEPTED = {
    ("whale_signals.py", "HLContext"):
        "string forward ref in a type annotation; never evaluated at "
        "runtime, imported lazily by callers",
}


def _pyflakes(paths) -> list:
    proc = subprocess.run(
        [sys.executable, "-m", "pyflakes", *[str(p) for p in paths]],
        capture_output=True, text=True, cwd=str(BOT_DIR))
    return [ln for ln in (proc.stdout + proc.stderr).splitlines()
            if "undefined name" in ln]


def _accepted(line: str) -> bool:
    for (fname, name), _why in _ACCEPTED.items():
        if fname in line and f"'{name}'" in line:
            return True
    return False


@pytest.fixture(scope="module")
def findings():
    pytest.importorskip("pyflakes")
    targets = sorted(BOT_DIR.glob("*.py")) + sorted((BOT_DIR / "tools").glob("*.py"))
    return [ln for ln in _pyflakes(targets) if not _accepted(ln)]


def test_no_undefined_names_in_the_codebase(findings):
    """A name used but never defined is code that cannot run."""
    assert not findings, (
        "undefined name(s) — this is the append_forming_bar bug class:\n  "
        + "\n  ".join(findings))


def test_the_linter_actually_catches_the_bug_shape(tmp_path):
    """A guard that cannot detect the thing it guards against is worse
    than none, because it reads as coverage. Reproduce the exact shape
    and assert pyflakes flags it."""
    pytest.importorskip("pyflakes")
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def helper():\n"
        "    import datetime as _dt\n"
        "    return _dt.date.today()\n"
        "\n"
        "def production_path(session=None):\n"
        "    try:\n"
        "        return session or _dt.date.today()\n"
        "    except Exception:\n"
        "        return None\n",
        encoding="utf-8")
    hits = _pyflakes([probe])
    assert any("_dt" in h for h in hits), \
        "pyflakes no longer detects the locally-scoped-import bug"


def test_every_acceptance_carries_a_reason():
    """An acceptance list without reasons becomes a place to bury
    findings. Each entry must say why it is not a defect."""
    for key, why in _ACCEPTED.items():
        assert why and len(why) > 20, f"{key} accepted without a reason"
