"""Whale E.2 — Arkham CEX-flow gate tests.

Phase W enhancement: turns the (lagging) HL leaderboard signal into a
leading-confirmation flow by gating entries against on-chain net flow.
Before opening whale LONG on coin X, query Arkham's /token/top_flow/{chain}
for last-24h net flow. If top entities are net distributors → skip LONG.
Mirror for SHORT.

Default OFF (WHALE_USE_ARKHAM_FLOW_GATE=false). Operator opts in by
setting that flag AND ARKHAM_API_KEY in .env.

Graceful degradation everywhere: missing API key, unknown coin, HTTP
failure, malformed response → return None / pass. The gate is purely
additive — when functioning, it strengthens conviction; when missing,
it never blocks an otherwise-good signal.

Run: python -m pytest tests/test_whale_arkham.py -v
"""

from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))


def _mock_urlopen_resp(payload):
    """Build a context-manager mock whose .__enter__().read() yields the
    JSON-encoded payload. Assign to mock_urlopen.return_value."""
    body = json.dumps(payload).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = MagicMock(read=lambda: body)
    mock_resp.__exit__.return_value = False
    return mock_resp


# ─── fetch_token_net_flow_24h ──────────────────────────────────────────────

def test_fetch_returns_none_when_no_api_key(monkeypatch):
    """No ARKHAM_API_KEY → return None without making any HTTP call."""
    from whale_arkham import fetch_token_net_flow_24h
    monkeypatch.delenv("ARKHAM_API_KEY", raising=False)
    assert fetch_token_net_flow_24h("ETH") is None


def test_fetch_returns_none_for_unmapped_coin(monkeypatch):
    """Coin we don't have a chain mapping for → return None (don't call API)."""
    from whale_arkham import fetch_token_net_flow_24h
    monkeypatch.setenv("ARKHAM_API_KEY", "test-key")
    assert fetch_token_net_flow_24h("OBSCURE_PERP") is None


@patch("whale_arkham.urllib.request.urlopen")
def test_fetch_uses_explicit_net_flow_field(mock_urlopen, monkeypatch):
    """When Arkham row has a `net_flow` field, use it directly."""
    from whale_arkham import fetch_token_net_flow_24h
    monkeypatch.setenv("ARKHAM_API_KEY", "test-key")
    mock_urlopen.return_value = _mock_urlopen_resp([
        {"symbol": "ETH", "net_flow": -2_500_000},
        {"symbol": "USDC", "net_flow": 8_000_000},
    ])
    val = fetch_token_net_flow_24h("ETH")
    assert val == -2_500_000.0


@patch("whale_arkham.urllib.request.urlopen")
def test_fetch_derives_net_from_inflow_minus_outflow(mock_urlopen, monkeypatch):
    """When no net_flow field, compute inflow - outflow."""
    from whale_arkham import fetch_token_net_flow_24h
    monkeypatch.setenv("ARKHAM_API_KEY", "test-key")
    mock_urlopen.return_value = _mock_urlopen_resp([
        {"symbol": "ETH", "inflow": 5_000_000, "outflow": 3_000_000},
    ])
    val = fetch_token_net_flow_24h("ETH")
    assert val == 2_000_000.0


@patch("whale_arkham.urllib.request.urlopen")
def test_fetch_returns_none_when_symbol_absent(mock_urlopen, monkeypatch):
    """Coin not in the response → None (gate degrades to pass)."""
    from whale_arkham import fetch_token_net_flow_24h
    monkeypatch.setenv("ARKHAM_API_KEY", "test-key")
    mock_urlopen.return_value = _mock_urlopen_resp([
        {"symbol": "USDC", "net_flow": 1_000_000},
    ])
    val = fetch_token_net_flow_24h("ETH")
    assert val is None


@patch("whale_arkham.urllib.request.urlopen")
def test_fetch_handles_envelope_response(mock_urlopen, monkeypatch):
    """Some APIs wrap their list in {'data': [...]} — accept that shape too."""
    from whale_arkham import fetch_token_net_flow_24h
    monkeypatch.setenv("ARKHAM_API_KEY", "test-key")
    mock_urlopen.return_value = _mock_urlopen_resp(
        {"data": [{"symbol": "ETH", "net_flow": 750_000}]}
    )
    val = fetch_token_net_flow_24h("ETH")
    assert val == 750_000.0


@patch("whale_arkham.urllib.request.urlopen")
def test_fetch_returns_none_on_http_error(mock_urlopen, monkeypatch):
    """Network/HTTP failure → None, no raise."""
    from whale_arkham import fetch_token_net_flow_24h
    monkeypatch.setenv("ARKHAM_API_KEY", "test-key")
    mock_urlopen.side_effect = RuntimeError("network down")
    val = fetch_token_net_flow_24h("ETH")
    assert val is None


# ─── check_arkham_flow_gate filter ─────────────────────────────────────────

def test_arkham_gate_passes_when_net_flow_none():
    from whale_filters import check_arkham_flow_gate
    ok, reason = check_arkham_flow_gate("LONG", None)
    assert ok is True
    assert reason == ""


def test_arkham_gate_blocks_long_on_significant_distribution():
    """Whales are EXITING positions over 24h → don't enter LONG."""
    from whale_filters import check_arkham_flow_gate
    ok, reason = check_arkham_flow_gate("LONG", -2_000_000.0, threshold_usd=1_000_000.0)
    assert ok is False
    assert "distribution" in reason.lower()


def test_arkham_gate_blocks_short_on_significant_accumulation():
    """Whales are ACCUMULATING over 24h → don't enter SHORT."""
    from whale_filters import check_arkham_flow_gate
    ok, reason = check_arkham_flow_gate("SHORT", +2_000_000.0, threshold_usd=1_000_000.0)
    assert ok is False
    assert "accumulation" in reason.lower()


def test_arkham_gate_passes_when_below_threshold():
    """Small net flow magnitude is noise — don't gate."""
    from whale_filters import check_arkham_flow_gate
    ok, _ = check_arkham_flow_gate("LONG", -500_000.0, threshold_usd=1_000_000.0)
    assert ok is True
    ok, _ = check_arkham_flow_gate("SHORT", +500_000.0, threshold_usd=1_000_000.0)
    assert ok is True


def test_arkham_gate_passes_when_aligned():
    """LONG with net accumulation = confirmation, not a block.
       SHORT with net distribution = confirmation, not a block."""
    from whale_filters import check_arkham_flow_gate
    ok, _ = check_arkham_flow_gate("LONG", +5_000_000.0)
    assert ok is True
    ok, _ = check_arkham_flow_gate("SHORT", -5_000_000.0)
    assert ok is True
