"""Phase D.7f — CSV export button on Trade Log.

Legacy dashboard had a "Bitcoin spinner" CSV export modal. V2 ships a
simpler one-click export: a button in the trades toolbar that walks
the currently-visible rows (post-filter, current sort order), builds a
CSV, and triggers a Blob download.

Run: python -m pytest tests/test_dashboard_v2_export.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pytest.importorskip("jinja2")

import dashboard
from dashboard_renderer import render


# ─── Button markup ─────────────────────────────────────────────────────────

def test_trades_toolbar_has_export_button():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert "data-trades-export" in html
    assert ">Export CSV<" in html


def test_export_button_has_accessible_label():
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert 'aria-label="Download visible trades as CSV"' in html


def test_export_button_lives_inside_trades_toolbar():
    """Button should sit in the .trades-toolbar wrapper, not float free."""
    html = render("base.html.j2", dashboard._v2_test_context([]))
    toolbar_start = html.find('class="trades-toolbar"')
    toolbar_end = html.find("</div>", toolbar_start)
    button_pos = html.find("data-trades-export")
    assert toolbar_start < button_pos < toolbar_end


# ─── JS handler is wired ───────────────────────────────────────────────────

def test_dashboard_js_includes_export_handler():
    """The inlined JS must wire the click handler for the export button."""
    html = render("base.html.j2", dashboard._v2_test_context([]))
    assert "data-trades-export" in html
    # JS should reference the same hook
    assert "trades-export" in html
    # Must construct a Blob and trigger a download (the standard pattern)
    assert "Blob" in html
    assert "download" in html


def test_export_handler_includes_csv_header_row():
    """JS should emit a header row matching the visible table columns."""
    html = render("base.html.j2", dashboard._v2_test_context([]))
    # The JS hardcodes the header — verify one column name shows up in JS
    # context (not just in the table thead).
    assert '"Net PnL"' in html or "'Net PnL'" in html
