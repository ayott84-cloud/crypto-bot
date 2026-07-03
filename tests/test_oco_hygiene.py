"""P1.2 — OCO lifecycle hygiene.

With TP+SL resting exchange-side (P1.1), the new failure class is
orphaned/stacked triggers: a previous trade's bracket surviving into a
new entry on the same symbol, or triggers outliving their position.
Mitigation (research: Binance/Bybit/ccxt-community standard practice):
  1. cancel_pending_orders(symbol) BEFORE every new entry
  2. per-cycle sweep: for symbols with resting triggers but no position,
     cancel them (covered by close paths calling cancel_pending_orders;
     the pre-entry cancel is the belt-and-suspenders second layer)

Run: python -m pytest tests/test_oco_hygiene.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))


def _scalp_state():
    return {"positions": {}, "scalp_cooldowns": {}}


def test_scalp_open_cancels_stale_triggers_before_entry(monkeypatch):
    """open_scalp_position must cancel pending triggers on the symbol
    BEFORE placing the new entry order."""
    import scalp_main
    import pandas as pd

    executor = MagicMock()
    call_order = []
    executor.cancel_pending_orders.side_effect = \
        lambda sym: call_order.append(("cancel", sym))
    executor.open_long.side_effect = \
        lambda *a, **kw: call_order.append(("open", a[0]))

    df = pd.DataFrame({
        "open": [100.0], "high": [101.0], "low": [99.0],
        "close": [100.5], "volume": [1000.0],
    })
    cfg = {"symbol": "BTCUSDT", "sl_pct": 1.5, "tp_pct": 3.0,
            "strategy_name": "BTC 5m Scalp", "new_high_lookback": 20}

    monkeypatch.setattr(scalp_main, "register_entry", lambda *a, **kw: None)
    scalp_main.open_scalp_position(executor, _scalp_state(), "BTC_5M",
                                      cfg, df, "LONG")

    assert ("cancel", "BTCUSDT") in call_order
    assert ("open", "BTCUSDT") in call_order
    assert call_order.index(("cancel", "BTCUSDT")) < \
           call_order.index(("open", "BTCUSDT"))


def test_crossover_open_cancels_stale_triggers_before_entry(monkeypatch):
    import crossover_main
    import pandas as pd

    executor = MagicMock()
    call_order = []
    executor.cancel_pending_orders.side_effect = \
        lambda sym: call_order.append(("cancel", sym))
    executor.open_long.side_effect = \
        lambda *a, **kw: call_order.append(("open", a[0]))

    df = pd.DataFrame({
        "open": [100.0], "high": [101.0], "low": [99.0],
        "close": [100.5], "volume": [1000.0],
    })
    cfg = {"symbol": "ETHUSDT", "sl_pct": 1.0, "tp_pct": 2.0,
            "sma_fast": 20, "sma_slow": 50,
            "strategy_name": "ETH 1h Crossover"}

    monkeypatch.setattr(crossover_main, "register_entry", lambda *a, **kw: None)
    crossover_main.open_crossover_position(executor, {"positions": {}},
                                              "ETH_1H", cfg, df, "LONG")

    assert ("cancel", "ETHUSDT") in call_order
    assert ("open", "ETHUSDT") in call_order
    assert call_order.index(("cancel", "ETHUSDT")) < \
           call_order.index(("open", "ETHUSDT"))


def test_cancel_failure_does_not_block_entry(monkeypatch):
    """A cancel API failure must not prevent the entry — log and proceed
    (the entry order itself will fail loudly if something is truly wrong)."""
    import scalp_main
    import pandas as pd

    executor = MagicMock()
    executor.cancel_pending_orders.side_effect = RuntimeError("api down")
    opened = []
    executor.open_long.side_effect = lambda *a, **kw: opened.append(a[0])

    df = pd.DataFrame({
        "open": [100.0], "high": [101.0], "low": [99.0],
        "close": [100.5], "volume": [1000.0],
    })
    cfg = {"symbol": "BTCUSDT", "sl_pct": 1.5, "tp_pct": 3.0,
            "strategy_name": "BTC 5m Scalp", "new_high_lookback": 20}
    monkeypatch.setattr(scalp_main, "register_entry", lambda *a, **kw: None)

    scalp_main.open_scalp_position(executor, _scalp_state(), "BTC_5M",
                                      cfg, df, "LONG")
    assert opened == ["BTCUSDT"]
