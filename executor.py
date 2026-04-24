"""WEEX API trading wrapper.

Imports the existing WeexContractClient from the weex-trader-skill
and provides high-level trading operations with DRY_RUN enforcement.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

# Add the skill scripts directory to path so we can import the client
_SKILL_DIR = os.path.join(
    os.path.expanduser("~"), ".claude", "skills", "weex-trader-skill", "scripts"
)
if _SKILL_DIR not in sys.path:
    sys.path.insert(0, _SKILL_DIR)

from weex_contract_api import WeexContractClient, ENDPOINTS, Endpoint  # noqa: E402

logger = logging.getLogger("crypto_bot.executor")


class Executor:
    """High-level wrapper around WeexContractClient."""

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.client = WeexContractClient(
            base_url=os.getenv("WEEX_API_BASE", "https://api-contract.weex.com"),
            timeout=float(os.getenv("WEEX_API_TIMEOUT", "15.0")),
            locale=os.getenv("WEEX_LOCALE", "en-US"),
            api_key=os.getenv("WEEX_API_KEY"),
            api_secret=os.getenv("WEEX_API_SECRET"),
            api_passphrase=os.getenv("WEEX_API_PASSPHRASE"),
        )
        # Cache for contract info (min qty, tick sizes)
        self._contract_info: Dict[str, dict] = {}

    # ── Internal helpers ─────────────────────────────────────────────────

    def _call(self, endpoint_key: str, query: Optional[dict] = None,
              body: Optional[dict] = None) -> dict:
        """Execute an API call and return the response."""
        ep = ENDPOINTS.get(endpoint_key)
        if not ep:
            raise ValueError(f"Unknown endpoint: {endpoint_key}")

        prepared = self.client.prepare_request(ep, query=query, body=body)
        result = self.client.send(prepared)

        if not result.get("ok"):
            logger.error("API call %s failed: %s", endpoint_key, result)
        return result

    def _mutating_call(self, endpoint_key: str, query: Optional[dict] = None,
                       body: Optional[dict] = None, action_desc: str = "") -> dict:
        """Execute a mutating (write) API call with dry-run guard."""
        if self.dry_run:
            logger.info("[DRY RUN] Would execute %s: query=%s body=%s | %s",
                        endpoint_key, json.dumps(query or {}),
                        json.dumps(body or {}), action_desc)
            return {"ok": True, "dry_run": True, "data": {"msg": "dry run simulated"}}

        return self._call(endpoint_key, query=query, body=body)

    # ── Market Data ──────────────────────────────────────────────────────

    def get_klines(self, symbol: str, interval: str, limit: int = 300) -> list:
        """Fetch OHLCV kline data."""
        result = self._call("market.get_klines", query={
            "symbol": symbol, "interval": interval, "limit": str(limit)
        })
        if result.get("ok") and result.get("data"):
            data = result["data"]
            # WEEX returns {"code": "0", "data": [...klines...]}
            if isinstance(data, dict):
                return data.get("data", [])
            return data
        return []

    def get_symbol_price(self, symbol: str) -> Optional[float]:
        """Get current mark/last price for a symbol."""
        result = self._call("market.get_symbol_price", query={"symbol": symbol})
        if result.get("ok") and result.get("data"):
            data = result["data"]
            if isinstance(data, dict):
                inner = data.get("data", {})
                if isinstance(inner, list) and inner:
                    return float(inner[0].get("price", 0))
                if isinstance(inner, dict):
                    return float(inner.get("price", 0))
        return None

    def get_ticker_24h(self, symbol: str = None) -> list:
        """Get 24h ticker statistics."""
        query = {"symbol": symbol} if symbol else {}
        result = self._call("market.get_ticker24h", query=query)
        if result.get("ok") and result.get("data"):
            data = result["data"]
            if isinstance(data, dict):
                return data.get("data", [])
        return []

    def get_funding_rate(self, symbol: str = None) -> list:
        """Get current funding rate(s)."""
        query = {"symbol": symbol} if symbol else {}
        result = self._call("market.get_current_funding_rate", query=query)
        if result.get("ok") and result.get("data"):
            data = result["data"]
            if isinstance(data, dict):
                return data.get("data", [])
        return []

    def get_contract_info(self, symbol: str = None) -> list:
        """Get contract specifications (min qty, tick size, etc.)."""
        query = {"symbol": symbol} if symbol else {}
        result = self._call("market.get_contract_info", query=query)
        if result.get("ok") and result.get("data"):
            data = result["data"]
            if isinstance(data, dict):
                symbols = data.get("data", {}).get("symbols", [])
                return symbols if isinstance(symbols, list) else []
        return []

    # ── Account Data ─────────────────────────────────────────────────────

    def get_account_balance(self) -> dict:
        """Get futures account balance."""
        result = self._call("account.get_account_balance")
        if result.get("ok") and result.get("data") is not None:
            data = result["data"]
            # WEEX may return data as list [{...}] OR as {"data": [...]}
            if isinstance(data, list) and data:
                return data[0]
            if isinstance(data, dict):
                inner = data.get("data", {})
                if isinstance(inner, list) and inner:
                    return inner[0]
                return inner
        return {}

    def get_all_positions(self) -> list:
        """Get all open positions (filtered to non-zero size)."""
        result = self._call("account.get_all_positions")
        if result.get("ok") and result.get("data"):
            data = result["data"]
            if isinstance(data, dict):
                positions = data.get("data", [])
                if isinstance(positions, list):
                    return [p for p in positions
                            if float(p.get("positionAmt", "0")) != 0]
        return []

    def get_order_history(self, symbol: str = None, start_time: int = None,
                          limit: int = 100) -> list:
        """Get completed order history."""
        query: dict = {"limit": str(limit)}
        if symbol:
            query["symbol"] = symbol
        if start_time:
            query["startTime"] = str(start_time)
        result = self._call("transaction.get_order_history", query=query)
        if result.get("ok") and result.get("data"):
            data = result["data"]
            if isinstance(data, dict):
                return data.get("data", [])
        return []

    def get_trade_details(self, symbol: str = None, order_id: str = None,
                          start_time: int = None, limit: int = 100) -> list:
        """Get trade fill details."""
        query: dict = {"limit": str(limit)}
        if symbol:
            query["symbol"] = symbol
        if order_id:
            query["orderId"] = order_id
        if start_time:
            query["startTime"] = str(start_time)
        result = self._call("transaction.get_trade_details", query=query)
        if result.get("ok") and result.get("data"):
            data = result["data"]
            if isinstance(data, dict):
                return data.get("data", [])
        return []

    def get_contract_bills(self, start_time: int = None, end_time: int = None,
                           income_type: str = None, limit: int = 100) -> list:
        """Get contract income/bills (funding fees, realized PnL, etc.)."""
        body: dict = {"limit": limit}
        if start_time:
            body["startTime"] = start_time
        if end_time:
            body["endTime"] = end_time
        if income_type:
            body["incomeType"] = income_type
        result = self._call("account.get_contract_bills", body=body)
        if result.get("ok") and result.get("data"):
            data = result["data"]
            if isinstance(data, dict):
                return data.get("data", [])
        return []

    # ── Trading Operations ───────────────────────────────────────────────

    def open_long(self, symbol: str, quantity: str,
                  sl_trigger_price: str = None) -> dict:
        """Open a LONG position with a market order."""
        body: dict = {
            "symbol": symbol,
            "side": "BUY",
            "positionSide": "LONG",
            "type": "MARKET",
            "quantity": quantity,
            "newClientOrderId": f"bot-{int(time.time()*1000)}-open",
        }
        if sl_trigger_price:
            body["slTriggerPrice"] = sl_trigger_price
            body["slWorkingType"] = "MARK_PRICE"

        return self._mutating_call(
            "transaction.place_order", body=body,
            action_desc=f"OPEN LONG {symbol} qty={quantity}"
        )

    def close_long_partial(self, symbol: str, quantity: str) -> dict:
        """Partially close a LONG position (sell portion at market)."""
        body = {
            "symbol": symbol,
            "side": "SELL",
            "positionSide": "LONG",
            "type": "MARKET",
            "quantity": quantity,
            "newClientOrderId": f"bot-{int(time.time()*1000)}-tp1",
        }
        return self._mutating_call(
            "transaction.place_order", body=body,
            action_desc=f"PARTIAL CLOSE LONG {symbol} qty={quantity}"
        )

    def close_long_full(self, symbol: str) -> dict:
        """Fully close a LONG position."""
        body = {
            "symbol": symbol,
            "positionSide": "LONG",
        }
        return self._mutating_call(
            "transaction.close_positions", body=body,
            action_desc=f"FULL CLOSE LONG {symbol}"
        )

    def place_sl_order(self, symbol: str, trigger_price: str,
                       quantity: str) -> dict:
        """Place a stop-loss conditional order on the exchange."""
        body = {
            "symbol": symbol,
            "positionSide": "LONG",
            "side": "SELL",
            "planType": "STOP_LOSS",
            "triggerPrice": trigger_price,
            "workingType": "MARK_PRICE",
            "quantity": quantity,
            "type": "MARKET",
        }
        return self._mutating_call(
            "transaction.place_tp_sl_order", body=body,
            action_desc=f"SL ORDER {symbol} trigger={trigger_price}"
        )

    def cancel_pending_orders(self, symbol: str) -> dict:
        """Cancel all pending/conditional orders for a symbol."""
        return self._mutating_call(
            "transaction.cancel_all_pending_orders",
            body={"symbol": symbol},
            action_desc=f"CANCEL ALL PENDING {symbol}"
        )

    # ── SHORT-side operations (used by whale bot) ────────────────────────

    def open_short(self, symbol: str, quantity: str,
                   sl_trigger_price: str = None) -> dict:
        """Open a SHORT position with a market order."""
        body: dict = {
            "symbol": symbol,
            "side": "SELL",
            "positionSide": "SHORT",
            "type": "MARKET",
            "quantity": quantity,
            "newClientOrderId": f"bot-{int(time.time()*1000)}-open-s",
        }
        if sl_trigger_price:
            body["slTriggerPrice"] = sl_trigger_price
            body["slWorkingType"] = "MARK_PRICE"

        return self._mutating_call(
            "transaction.place_order", body=body,
            action_desc=f"OPEN SHORT {symbol} qty={quantity}"
        )

    def close_short_full(self, symbol: str) -> dict:
        """Fully close a SHORT position."""
        body = {
            "symbol": symbol,
            "positionSide": "SHORT",
        }
        return self._mutating_call(
            "transaction.close_positions", body=body,
            action_desc=f"FULL CLOSE SHORT {symbol}"
        )

    def place_sl_order_short(self, symbol: str, trigger_price: str,
                             quantity: str) -> dict:
        """Place a stop-loss on a SHORT position (buy to close when price rises)."""
        body = {
            "symbol": symbol,
            "positionSide": "SHORT",
            "side": "BUY",
            "planType": "STOP_LOSS",
            "triggerPrice": trigger_price,
            "workingType": "MARK_PRICE",
            "quantity": quantity,
            "type": "MARKET",
        }
        return self._mutating_call(
            "transaction.place_tp_sl_order", body=body,
            action_desc=f"SL ORDER SHORT {symbol} trigger={trigger_price}"
        )

    def place_tp_order(self, symbol: str, direction: str, trigger_price: str,
                       quantity: str) -> dict:
        """Place a take-profit conditional order for a LONG or SHORT position.

        direction: "LONG" or "SHORT" — determines side used to close.
        """
        if direction == "LONG":
            position_side, side = "LONG", "SELL"
        elif direction == "SHORT":
            position_side, side = "SHORT", "BUY"
        else:
            raise ValueError(f"direction must be LONG or SHORT, got {direction}")

        body = {
            "symbol": symbol,
            "positionSide": position_side,
            "side": side,
            "planType": "TAKE_PROFIT",
            "triggerPrice": trigger_price,
            "workingType": "MARK_PRICE",
            "quantity": quantity,
            "type": "MARKET",
        }
        return self._mutating_call(
            "transaction.place_tp_sl_order", body=body,
            action_desc=f"TP ORDER {direction} {symbol} trigger={trigger_price}"
        )

    # ── Contract Info Cache ──────────────────────────────────────────────

    def load_contract_info(self, symbols: List[str]) -> None:
        """Fetch and cache contract specs for the given symbols."""
        all_info = self.get_contract_info()
        for info in all_info:
            sym = info.get("symbol", "")
            if sym in symbols:
                self._contract_info[sym] = info
                logger.info("Cached contract info for %s: minQty=%s, tickSize=%s",
                            sym, info.get("minQty"), info.get("tickSize"))

    def get_min_qty(self, symbol: str) -> float:
        """Get minimum order quantity for a symbol."""
        info = self._contract_info.get(symbol, {})
        return float(info.get("minQty", "0.001"))

    def get_tick_size(self, symbol: str) -> float:
        """Get price tick size for a symbol."""
        info = self._contract_info.get(symbol, {})
        return float(info.get("tickSize", "0.01"))

    def get_qty_step(self, symbol: str) -> float:
        """Get quantity step size for a symbol."""
        info = self._contract_info.get(symbol, {})
        return float(info.get("stepSize", info.get("minQty", "0.001")))
