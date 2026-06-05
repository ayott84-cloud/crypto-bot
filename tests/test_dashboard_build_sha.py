"""Phase D.7c — BUILD_SHA resolution.

The colophon's BUILD chip should show the actual deploy commit, not "LOCAL".
We resolve via this precedence:
  1. GIT_SHA env var (deployer can override)
  2. `git rev-parse --short=8 HEAD` if the bot is running inside a git tree
  3. "local" fallback

Run: python -m pytest tests/test_dashboard_build_sha.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

import dashboard


def test_env_var_takes_precedence(monkeypatch):
    monkeypatch.setenv("GIT_SHA", "deadbeef12345")
    assert dashboard._resolve_build_sha() == "deadbeef"


def test_env_var_truncated_to_eight_chars(monkeypatch):
    monkeypatch.setenv("GIT_SHA", "fffffffffffffff")
    assert dashboard._resolve_build_sha() == "ffffffff"


def test_falls_back_to_git_when_env_unset(monkeypatch):
    """Without GIT_SHA, the resolver should ask git for HEAD short-sha."""
    monkeypatch.delenv("GIT_SHA", raising=False)
    sha = dashboard._resolve_build_sha()
    # In CI / dev environments there should be a real git tree, so we get
    # a hex string. If we don't (no .git directory), we fall back to "local".
    assert sha == "local" or (len(sha) == 8 and all(c in "0123456789abcdef" for c in sha))


def test_returns_local_when_git_unavailable(monkeypatch, tmp_path):
    """When neither GIT_SHA nor a git tree exists, return 'local'."""
    monkeypatch.delenv("GIT_SHA", raising=False)
    # Point the resolver at a non-git directory
    monkeypatch.setattr(dashboard, "BOT_DIR", tmp_path)
    assert dashboard._resolve_build_sha() == "local"


def test_empty_env_var_falls_through_to_git(monkeypatch):
    """An empty GIT_SHA should be treated as unset, not as a literal ''."""
    monkeypatch.setenv("GIT_SHA", "")
    sha = dashboard._resolve_build_sha()
    assert sha != ""
