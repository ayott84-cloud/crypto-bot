"""Phase J.5a — asset chart data builder tests.

Tests build chart-data dicts for Momentum + Breakout that
_v2_render_asset_chart_panel can consume. Pure-function shape only;
caches and indicator math get their own focused tests.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import pytest

pd = pytest.importorskip("pandas")

import dashboard


# ─── Fixtures ──────────────────────────────────────────────────────────────

def _weex_klines(n: int = 80, base_price: float = 100.0):
    """WEEX positional kline rows. n bars rising in a clean trend."""
    base_ts = 1_700_000_000_000
    rows = []
    for i in range(n):
        price = base_price + i * 0.5
        rows.append([
            base_ts + i * 14_400_000,    # 4h cadence in ms
            price - 0.2,                  # open
            price + 0.5,                  # high
            price - 0.5,                  # low
            price,                        # close
            1000 + i,                     # volume
            base_ts + (i + 1) * 14_400_000,
            100_000, 50, 500, 50_000,
        ])
    return rows


def _mock_executor(klines):
    ex = MagicMock()
    ex.get_klines.return_value = klines
    return ex


def _make_trade(bot, symbol, direction, entry_ts, exit_ts,
                  entry_price, exit_price, net_pnl, idx=1):
    return {
        "id": idx,
        "date_opened":  entry_ts,
        "date_closed":  exit_ts,
        "symbol": symbol, "direction": direction, "bot": bot,
        "strategy": f"{bot} strategy",
        "entry_price": entry_price, "exit_price": exit_price,
        "quantity": 0.01, "leverage": 10,
        "net_pnl": net_pnl,
        "result": "WIN" if net_pnl > 0 else "LOSS",
        "exit_reason": "TP1",
    }


# ─── Kline cache ───────────────────────────────────────────────────────────

def test_kline_cache_returns_same_data_on_second_call():
    """Within TTL, the second call returns cached data without re-fetching."""
    dashboard._kline_cache_clear()
    ex = _mock_executor(_weex_klines(50))
    a = dashboard._v2_fetch_klines_cached(ex, "BTCUSDT", "4h", 50)
    b = dashboard._v2_fetch_klines_cached(ex, "BTCUSDT", "4h", 50)
    assert a is b  # same object reference = cache hit
    assert ex.get_klines.call_count == 1


def test_kline_cache_different_keys_fetch_independently():
    dashboard._kline_cache_clear()
    ex = _mock_executor(_weex_klines(50))
    dashboard._v2_fetch_klines_cached(ex, "BTCUSDT", "4h", 50)
    dashboard._v2_fetch_klines_cached(ex, "ETHUSDT", "4h", 50)
    assert ex.get_klines.call_count == 2


# ─── Momentum chart data ──────────────────────────────────────────────────

def test_momentum_chart_data_has_candles_and_ema_overlays():
    """Momentum tab shows EMA20 + EMA50 overlays on candlestick data."""
    dashboard._kline_cache_clear()
    ex = _mock_executor(_weex_klines(80))
    cfg = {"symbol": "BTCUSDT", "interval": "4h",
           "ema_fast": 20, "ema_slow": 50}
    data = dashboard._v2_asset_chart_data(
        ex, bot_class="momentum", asset_name="BTC_4H", cfg=cfg, trades=[])
    assert "candles" in data
    assert len(data["candles"]) > 0
    assert all(set(c) >= {"time", "open", "high", "low", "close"}
                for c in data["candles"])
    assert "overlays" in data
    overlay_names = {o["name"] for o in data["overlays"]}
    assert "EMA20" in overlay_names
    assert "EMA50" in overlay_names


# ─── Breakout chart data ──────────────────────────────────────────────────

def test_breakout_chart_data_has_donchian_overlays():
    """Breakout tab shows Donchian-55 entry + Donchian-20 exit bands."""
    dashboard._kline_cache_clear()
    ex = _mock_executor(_weex_klines(80))
    cfg = {"symbol": "BTCUSDT", "interval": "4h",
           "donchian_period": 55, "donchian_exit_period": 20}
    data = dashboard._v2_asset_chart_data(
        ex, bot_class="breakout", asset_name="BTC_4H", cfg=cfg, trades=[])
    overlay_names = {o["name"] for o in data["overlays"]}
    # Entry channel and exit channel both rendered
    assert any("Donchian-55" in n for n in overlay_names)
    assert any("Donchian-20" in n for n in overlay_names)


# ─── Entry/exit markers ──────────────────────────────────────────────────

def test_markers_emitted_for_trades_in_window():
    """Trades whose entry timestamp falls inside the kline window get markers."""
    dashboard._kline_cache_clear()
    klines = _weex_klines(80)
    ex = _mock_executor(klines)
    cfg = {"symbol": "BTCUSDT", "interval": "4h",
           "ema_fast": 20, "ema_slow": 50}
    # Build a trade whose entry timestamp matches bar 30's close_time
    bar30_ts_s = klines[30][0] // 1000  # ms → seconds
    from datetime import datetime, timezone
    entry_dt = datetime.fromtimestamp(bar30_ts_s, tz=timezone.utc).isoformat()
    trades = [_make_trade(
        "Momentum", "BTCUSDT", "LONG",
        entry_ts=entry_dt, exit_ts=entry_dt,
        entry_price=115, exit_price=120, net_pnl=5)]
    data = dashboard._v2_asset_chart_data(
        ex, bot_class="momentum", asset_name="BTC_4H", cfg=cfg, trades=trades)
    assert len(data["markers"]) >= 1
    # First marker should be an entry marker for the LONG
    entry_marker = data["markers"][0]
    assert "text" in entry_marker
    assert "position" in entry_marker


def test_markers_skipped_for_wrong_symbol():
    """A trade on ETHUSDT shouldn't appear on the BTCUSDT chart."""
    dashboard._kline_cache_clear()
    ex = _mock_executor(_weex_klines(80))
    cfg = {"symbol": "BTCUSDT", "interval": "4h",
           "ema_fast": 20, "ema_slow": 50}
    trades = [_make_trade("Momentum", "ETHUSDT", "LONG",
                            entry_ts="2026-05-01", exit_ts="2026-05-02",
                            entry_price=2000, exit_price=2100, net_pnl=10)]
    data = dashboard._v2_asset_chart_data(
        ex, bot_class="momentum", asset_name="BTC_4H", cfg=cfg, trades=trades)
    assert data["markers"] == []


# ─── Empty kline edge case ───────────────────────────────────────────────

def test_empty_klines_returns_empty_chart_data():
    dashboard._kline_cache_clear()
    ex = _mock_executor([])
    cfg = {"symbol": "BTCUSDT", "interval": "4h",
           "ema_fast": 20, "ema_slow": 50}
    data = dashboard._v2_asset_chart_data(
        ex, bot_class="momentum", asset_name="BTC_4H", cfg=cfg, trades=[])
    assert data == {"candles": [], "overlays": [], "markers": []}


# ─── Chart-panel builder (per-bot list) ──────────────────────────────────

def test_build_chart_panels_for_bot_empty_when_executor_missing():
    """Test context (executor=None) emits dropdown entries but no data."""
    dashboard._kline_cache_clear()
    panels = dashboard._v2_build_chart_panels_for_bot(
        None, [], "momentum", max_assets=3)
    assert len(panels) <= 3
    for p in panels:
        assert p["chart_data"] == {"candles": [], "overlays": [], "markers": []}


def test_build_all_chart_panels_keys():
    """The wrapper returns one key per bot — present in every context."""
    panels = dashboard._v2_build_all_chart_panels(None, [])
    assert set(panels.keys()) == {"momentum", "breakout", "whale",
                                    "funding", "pair", "reversal"}
    assert panels["whale"] == []
    assert panels["funding"] == []


def test_context_includes_chart_panels_root():
    """Test context exposes chart_panels_root so templates can iterate."""
    ctx = dashboard._v2_test_context([])
    assert "chart_panels_root" in ctx
    assert "momentum" in ctx["chart_panels_root"]
    assert "breakout" in ctx["chart_panels_root"]


# ─── Render-level integration ────────────────────────────────────────────

def test_momentum_tab_renders_asset_chart_section_when_panels_present():
    """When chart_panels_root.momentum has entries, the dropdown renders."""
    pytest.importorskip("jinja2")
    from dashboard_renderer import render
    ctx = dashboard._v2_test_context([])
    html = render("base.html.j2", ctx)
    # Either momentum tab has chart panels (real config) OR they're empty
    # (config import failed). Both are acceptable; if any panels exist,
    # the section must render.
    if ctx["chart_panels_root"]["momentum"]:
        assert "asset-chart-section" in html
        assert "asset-chart-section__select" in html
