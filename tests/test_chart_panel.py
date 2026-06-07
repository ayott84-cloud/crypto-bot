"""Phase J.2 — _v2_render_asset_chart_panel tests.

The helper emits HTML that initializes a TradingView Lightweight Charts
instance from inlined JSON data. Verify shape, not visual output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

import dashboard


def test_chart_panel_emits_container_div_with_unique_id():
    html = dashboard._v2_render_asset_chart_panel(
        "btc_4h", chart_data={"candles": [], "overlays": [], "markers": []})
    assert 'id="chart-btc_4h"' in html
    assert 'class="asset-chart"' in html


def test_chart_panel_emits_height_style():
    html = dashboard._v2_render_asset_chart_panel(
        "x", chart_data={"candles": [], "overlays": [], "markers": []}, height_px=520)
    assert "height:520px" in html.replace(" ", "")


def test_chart_panel_inlines_json_data_block():
    """The data goes into a <script type=application/json> so init code can JSON.parse it."""
    data = {"candles": [{"time": 1, "open": 10, "high": 11, "low": 9, "close": 10}],
             "overlays": [], "markers": []}
    html = dashboard._v2_render_asset_chart_panel("x", chart_data=data)
    assert '<script type="application/json"' in html
    assert 'id="chartdata-x"' in html
    # The JSON itself is embedded; pull it out and verify shape
    import re
    m = re.search(r'id="chartdata-x">\s*(\{.*?\})\s*</script>', html, re.DOTALL)
    assert m is not None
    parsed = json.loads(m.group(1))
    assert parsed["candles"][0]["close"] == 10


def test_chart_panel_init_script_calls_init_function():
    """The init <script> calls initAssetChart('chart_id', dataElement)."""
    html = dashboard._v2_render_asset_chart_panel(
        "btc_4h", chart_data={"candles": [], "overlays": [], "markers": []})
    assert "initAssetChart" in html
    assert "'btc_4h'" in html or '"btc_4h"' in html


def test_chart_panel_escapes_chart_id_safely():
    """Chart IDs are sanitized so injection attempts don't survive."""
    html = dashboard._v2_render_asset_chart_panel(
        "evil\"<script>alert(1)</script>",
        chart_data={"candles": [], "overlays": [], "markers": []})
    # The dangerous content should be CSS-class-safe (alphanumeric + _)
    assert "<script>alert(1)</script>" not in html
    assert "evil" in html  # The safe prefix survives


def test_chart_panel_handles_empty_data_gracefully():
    """Empty arrays don't crash the render."""
    html = dashboard._v2_render_asset_chart_panel(
        "x", chart_data={"candles": [], "overlays": [], "markers": []})
    assert "asset-chart" in html
    assert '"candles": []' in html or '"candles":[]' in html
