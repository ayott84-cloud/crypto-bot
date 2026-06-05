"""Unit tests for dashboard_renderer (Phase D.0 scaffolding).

Verifies the foundation: feature flag is off by default, and the static-asset
inliner correctly substitutes <link> and <script> tags with the actual file
contents.

Run: python -m pytest tests/test_dashboard_renderer.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

import dashboard_renderer


# ─── Feature flag ──────────────────────────────────────────────────────────

def test_dashboard_v2_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DASHBOARD_V2", raising=False)
    assert dashboard_renderer.dashboard_v2_enabled() is False


def test_dashboard_v2_enabled_when_flag_set(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2", "true")
    assert dashboard_renderer.dashboard_v2_enabled() is True


def test_dashboard_v2_accepts_alternate_truthy_values(monkeypatch):
    for v in ("1", "yes", "TRUE", "True"):
        monkeypatch.setenv("DASHBOARD_V2", v)
        assert dashboard_renderer.dashboard_v2_enabled() is True


def test_dashboard_v2_disabled_for_explicit_false_or_garbage(monkeypatch):
    for v in ("false", "0", "no", "off", "asdf"):
        monkeypatch.setenv("DASHBOARD_V2", v)
        assert dashboard_renderer.dashboard_v2_enabled() is False


# ─── Static-asset inlining ─────────────────────────────────────────────────

def test_inline_static_replaces_css_link_with_style_tag(tmp_path, monkeypatch):
    """A <link rel="stylesheet" href="/static/css/X.css"> tag becomes <style>X-contents</style>."""
    # Stand up a fake static/ tree
    css_dir = tmp_path / "css"
    css_dir.mkdir()
    (css_dir / "tokens.css").write_text(":root { --x: 1; }", encoding="utf-8")
    monkeypatch.setattr(dashboard_renderer, "_STATIC_DIR", tmp_path)

    html = '<head><link rel="stylesheet" href="/static/css/tokens.css"></head>'
    out = dashboard_renderer._inline_static(html)

    assert "<link rel=" not in out
    assert "<style>" in out
    assert ":root { --x: 1; }" in out


def test_inline_static_replaces_script_src_with_inline_script(tmp_path, monkeypatch):
    js_dir = tmp_path / "js"
    js_dir.mkdir()
    (js_dir / "app.js").write_text("console.log('hello');", encoding="utf-8")
    monkeypatch.setattr(dashboard_renderer, "_STATIC_DIR", tmp_path)

    html = '<body><script src="/static/js/app.js"></script></body>'
    out = dashboard_renderer._inline_static(html)

    assert "<script src=" not in out
    assert "console.log('hello');" in out


def test_inline_static_handles_missing_file_gracefully(tmp_path, monkeypatch):
    """A missing static asset should not crash the build; just empty inline."""
    monkeypatch.setattr(dashboard_renderer, "_STATIC_DIR", tmp_path)
    html = '<link rel="stylesheet" href="/static/css/missing.css">'
    # Should not raise
    out = dashboard_renderer._inline_static(html)
    # And should not still have the <link> tag dangling
    assert "<link rel=" not in out


def test_inline_static_handles_multiple_assets(tmp_path, monkeypatch):
    (tmp_path / "css").mkdir()
    (tmp_path / "js").mkdir()
    (tmp_path / "css" / "a.css").write_text("a{}", encoding="utf-8")
    (tmp_path / "css" / "b.css").write_text("b{}", encoding="utf-8")
    (tmp_path / "js" / "c.js").write_text("/*c*/", encoding="utf-8")
    monkeypatch.setattr(dashboard_renderer, "_STATIC_DIR", tmp_path)

    html = (
        '<link rel="stylesheet" href="/static/css/a.css">'
        '<link rel="stylesheet" href="/static/css/b.css">'
        '<script src="/static/js/c.js"></script>'
    )
    out = dashboard_renderer._inline_static(html)
    assert "a{}" in out and "b{}" in out and "/*c*/" in out
    assert out.count("<link rel=") == 0
    assert out.count("<script src=") == 0


# ─── tokens.css sanity check ────────────────────────────────────────────────

def test_tokens_css_file_exists_with_expected_custom_properties():
    """Quant Cockpit tokens must exist with the canonical names the
    templates will reference. Drift here = templates break."""
    tokens = (BOT_DIR / "static" / "css" / "tokens.css").read_text(encoding="utf-8")
    for prop in (
        "--surface-0", "--surface-1", "--ink-1", "--ink-2", "--ink-3",
        "--up", "--down", "--warn",
        "--b-momentum", "--b-whale", "--b-funding",
        "--font-ui", "--font-mono", "--font-display",
        "--motion-breath",
    ):
        assert prop in tokens, f"Token {prop} missing from tokens.css"
