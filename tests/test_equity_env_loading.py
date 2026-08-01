"""Module 2 — the equity modules must load .env themselves.

Found on the first real droplet run: validate_stock_sleeves reported
"TIINGO_API_KEY not set" on a box where the key WAS in .env. Cause:
every crypto module gets dotenv loading as a side effect of importing
config.py, and the equity modules deliberately don't import config (so
Module 3 can reuse them without dragging in the crypto universe). Nobody
called load_dotenv, so os.getenv saw nothing.

The failure was at least loud and correctly attributed — a credentials
error, not a silent empty dataset — but it still cost a round trip.

Run: python -m pytest tests/test_equity_env_loading.py -v
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest


def test_equity_bars_loads_dotenv_at_import(tmp_path, monkeypatch):
    """A key present only in crypto_bot/.env must be visible after
    importing tools._equity_bars with nothing else imported first."""
    env = tmp_path / ".env"
    env.write_text("TIINGO_API_KEY=from-dotenv-file\n", encoding="utf-8")

    import tools._equity_bars as eb
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    monkeypatch.setattr(eb, "BOT_DIR", tmp_path)
    eb._load_env()
    import os
    assert os.getenv("TIINGO_API_KEY") == "from-dotenv-file"


def test_load_env_is_safe_when_no_dotenv_exists(tmp_path, monkeypatch):
    import tools._equity_bars as eb
    monkeypatch.setattr(eb, "BOT_DIR", tmp_path / "nonexistent")
    eb._load_env()          # must not raise


def test_load_env_does_not_clobber_a_real_environment_variable(tmp_path,
                                                                 monkeypatch):
    """systemd passes credentials via EnvironmentFile; a .env on disk
    must not override what the service was actually started with."""
    env = tmp_path / ".env"
    env.write_text("TIINGO_API_KEY=from-dotenv-file\n", encoding="utf-8")
    import tools._equity_bars as eb
    monkeypatch.setenv("TIINGO_API_KEY", "from-real-env")
    monkeypatch.setattr(eb, "BOT_DIR", tmp_path)
    eb._load_env()
    import os
    assert os.getenv("TIINGO_API_KEY") == "from-real-env"


def test_credentials_are_read_lazily_not_frozen_at_import(monkeypatch):
    """Module-level constants captured at import cannot see a key set
    afterwards. The accessor must re-read so ordering stops mattering."""
    import tools._equity_bars as eb
    monkeypatch.setattr(eb, "TIINGO_API_KEY", "")
    monkeypatch.setenv("TIINGO_API_KEY", "set-after-import")
    assert eb.tiingo_key() == "set-after-import"


def test_explicit_module_constant_still_wins_for_tests(monkeypatch):
    import tools._equity_bars as eb
    monkeypatch.setattr(eb, "TIINGO_API_KEY", "explicit")
    monkeypatch.setenv("TIINGO_API_KEY", "from-env")
    assert eb.tiingo_key() == "explicit"


def test_alpaca_keys_use_the_same_accessor(monkeypatch):
    import tools._equity_bars as eb
    monkeypatch.setattr(eb, "ALPACA_API_KEY", "")
    monkeypatch.setattr(eb, "ALPACA_API_SECRET", "")
    monkeypatch.setenv("ALPACA_API_KEY", "ak")
    monkeypatch.setenv("ALPACA_API_SECRET", "as")
    assert eb.alpaca_keys() == ("ak", "as")


def test_credential_status_masks_secrets():
    """The validator prints a status line so 'not set' is
    distinguishable from 'set but not loaded' — it must never print the
    key itself."""
    import tools._equity_bars as eb
    status = eb.credential_status()
    assert isinstance(status, dict)
    for v in status.values():
        assert v in ("set", "MISSING"), f"leaked a credential value: {v!r}"
