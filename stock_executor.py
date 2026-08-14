"""Module 2 Phase S3 — Alpaca paper executor.

Duck-typed to the same surface the crypto bot mains already call on
executor.Executor, so stock_daily_main reads like breakout_main and the
shared helpers (position_manager sizing/reconcile, notifier, journal)
work unchanged. This is the seam Module 3 (forex, IBKR) will copy.

THREE SAFETY PROPERTIES, IN ORDER OF HOW BADLY THEY WOULD HURT:

1. PAPER ONLY. base_url is paper-api.alpaca.markets. Constructing a
   live executor raises unless ALLOW_LIVE_TRADING is explicitly set,
   and even then it announces itself loudly. The crypto fleet has run
   DRY_RUN for months precisely because that flip must be a deliberate
   human act; a stocks module that could quietly point at real money
   would throw that discipline away.

2. DRY_RUN GUARD. Mirrors executor._mutating_call — with dry_run set,
   no mutating request leaves the process. Reads still go out, because
   a paper run wants real prices.

3. LONG/FLAT ONLY. The daily sleeves never short. The short methods
   RAISE rather than quietly succeeding: Reg SHO short-sale
   restrictions, locates and borrow fees are entirely unmodeled here,
   and a silent short would be trading a strategy nobody validated.

Perp-only concepts (funding rates) raise NotImplementedError rather
than returning a neutral zero — a zero would silently satisfy a crypto
filter that has no meaning for equities.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional

BOT_DIR = Path(__file__).resolve().parent

logger = logging.getLogger("crypto_bot.stock_executor")

# One fetcher, not two. The live daemon and the backtest now read equity
# bars through the same ranged, paginated, completeness-checked path.
from tools._equity_bars import (          # noqa: E402
    fetch_daily as _fetch_daily,
    to_positional_rows as _to_positional_rows,
)

PAPER_BASE = "https://paper-api.alpaca.markets"
LIVE_BASE = "https://api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets/v2/stocks"

_TIMEOUT_S = 30.0
_RETRY_SLEEPS = (2.0, 5.0)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _load_env() -> None:
    """Same two-path .env load as tools/_equity_bars — this module does
    not import config either, and the Aug 1 droplet run proved that a
    missing load_dotenv reads as 'no credentials'."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    try:
        load_dotenv(BOT_DIR / ".env")
        load_dotenv(BOT_DIR.parent / ".env")
    except OSError:
        pass


_load_env()

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET", "")

# Deliberately NOT read from the environment by default. Flipping this
# is a code edit + a conscious decision, not an env typo away.
ALLOW_LIVE_TRADING = False


class CredentialsMissing(Exception):
    """Alpaca keys absent."""


class LiveTradingBlocked(Exception):
    """Someone asked for live trading without the explicit opt-in."""


class ShortingNotSupported(Exception):
    """Module 2's sleeves are long/flat. Reg SHO locates, borrow fees
    and short-sale restrictions are unmodeled, so shorting is refused
    rather than silently attempted."""


def _keys() -> tuple:
    return (ALPACA_API_KEY or os.getenv("ALPACA_API_KEY", ""),
             ALPACA_API_SECRET or os.getenv("ALPACA_API_SECRET", ""))


def _http(method: str, url: str, headers: dict = None,
            params: dict = None, body: dict = None):
    """One HTTP call with backoff on transient status codes."""
    q = urllib.parse.urlencode({k: v for k, v in (params or {}).items()
                                  if v is not None})
    full = f"{url}?{q}" if q else url
    data = json.dumps(body).encode() if body is not None else None
    last = None
    for i, sleep_s in enumerate((0.0,) + _RETRY_SLEEPS):
        if sleep_s:
            time.sleep(sleep_s)
        req = urllib.request.Request(full, data=data, method=method,
                                       headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r:
                raw = r.read().decode("utf-8", "replace")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code in _RETRYABLE_STATUS:
                last = f"HTTP {e.code}"
                continue
            detail = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"Alpaca HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = str(e)
            continue
    raise RuntimeError(f"Alpaca request failed after retries: {last}")


_INTERVAL_TO_TIMEFRAME = {
    "1m": "1Min", "5m": "5Min", "15m": "15Min", "30m": "30Min",
    "1h": "1Hour", "1d": "1Day",
}


class StockExecutor:
    """Alpaca paper-trading executor with the crypto Executor's surface."""

    def __init__(self, dry_run: bool = True, paper: bool = True):
        key, secret = _keys()
        if not (key and secret):
            raise CredentialsMissing(
                "ALPACA_API_KEY / ALPACA_API_SECRET not visible to this "
                f"process (checked the environment and {BOT_DIR / '.env'}). "
                "Paper keys are instant at alpaca.markets.")
        if not paper and not ALLOW_LIVE_TRADING:
            raise LiveTradingBlocked(
                "Live trading is blocked. Module 2 runs on paper until an "
                "explicit operator decision — set ALLOW_LIVE_TRADING in "
                "stock_executor.py, deliberately, and only after the "
                "P4 path-to-live checklist is satisfied.")
        self.dry_run = bool(dry_run)
        self.paper = bool(paper)
        self.base_url = PAPER_BASE if paper else LIVE_BASE
        self._headers = {"APCA-API-KEY-ID": key,
                          "APCA-API-SECRET-KEY": secret,
                          "Content-Type": "application/json"}
        if not paper:
            print("*** LIVE TRADING ENABLED — real money is at risk ***")
            logger.warning("LIVE trading enabled on Alpaca")
        logger.info("StockExecutor ready (paper=%s, dry_run=%s)",
                     self.paper, self.dry_run)

    # ─── mutating guard ──────────────────────────────────────────────

    def _mutate(self, action: str, fn):
        """The DRY_RUN seam. Mirrors executor._mutating_call so a paper
        run can exercise the whole code path without placing orders."""
        if self.dry_run:
            logger.info("[DRY_RUN] %s", action)
            return {"ok": True, "dry_run": True, "action": action}
        return fn()

    # ─── reads ───────────────────────────────────────────────────────

    def get_klines(self, symbol: str, interval: str = "1d",
                     limit: int = 300) -> list:
        """Bars in the WEEX 11-column positional shape.

        Delegates to tools/_equity_bars.fetch_daily rather than issuing
        its own request. This used to send Alpaca a bare `limit` with no
        start/end and no cursor pagination, which came back far short of
        `limit` — Module 2 sat at blocked_by="insufficient_history" for
        the twelve days after it was unpaused, because the reversion
        sleeve needs sma_period + 2 = 202 daily bars and never got them.

        The validated fetcher has done ranged, paginated, completeness-
        checked requests since S1. Keeping a second, weaker fetcher in
        the live path IS the defect — so there is now only one.

        signals.build_dataframe reads df["close_time"] unconditionally,
        so the 11-column layout is mandatory, and rows must be
        chronological because every replay and indicator assumes it.
        """
        if str(interval).lower() not in ("1d", "1day", "d"):
            # S5 has not built the intraday path. A short series would
            # read as data; refusing says what is actually true.
            raise NotImplementedError(
                f"equity interval {interval!r} needs the S5 intraday "
                f"fetcher — only daily bars are wired today")

        want = int(limit)
        end = _dt.date.today()
        # 252 trading days per calendar year: asking for `want` sessions
        # over `want` CALENDAR days lands ~40% short. Pad on top of the
        # ratio so holidays cannot eat into the requested count.
        span = int(want * 365 / 252) + 15
        start = end - _dt.timedelta(days=span)

        # Tiingo first, matching the S2 validation runs exactly — using a
        # different vendor live than in the backtest is the divergence
        # this delegation exists to kill. Alpaca free tier also answers
        # recent daily bars with HTTP 403 ("subscription does not permit
        # querying recent SIP data"), so it cannot be the primary for a
        # daemon that asks for data up to today.
        df = None
        errors = []
        for provider in ("tiingo", "alpaca"):
            try:
                df = _fetch_daily(symbol.upper(), start, end,
                                    provider=provider, verify_complete=True)
                if df is not None and len(df):
                    break
                errors.append(f"{provider}: empty")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{provider}: {e}")
                df = None
        if df is None or not len(df):
            # The daemon reads [] as "skip this symbol this cycle". An
            # exception here would take the whole cycle down with it.
            logger.warning("equity bar fetch failed for %s — %s",
                            symbol, "; ".join(errors))
            return []

        rows = _to_positional_rows(df, "1d")
        if len(rows) < want:
            # The silence that hid this bug for twelve days.
            logger.warning(
                "SHORT equity series for %s: got %d of %d requested bars "
                "(%s..%s)", symbol, len(rows), want, start, end)
        rows.sort(key=lambda r: r[0])
        return rows

    def get_symbol_price(self, symbol: str) -> Optional[float]:
        try:
            payload = _http("GET",
                             f"{DATA_BASE}/{symbol.upper()}/trades/latest",
                             headers=self._headers)
            p = ((payload or {}).get("trade") or {}).get("p")
            return float(p) if p is not None else None
        except Exception as e:  # noqa: BLE001
            logger.warning("price fetch failed for %s: %s", symbol, e)
            return None

    def get_account_balance(self) -> dict:
        """`balance` key because every close path reads .get('balance')."""
        try:
            a = _http("GET", f"{self.base_url}/v2/account",
                       headers=self._headers) or {}
            return {"balance": float(a.get("equity") or 0.0),
                     "cash": float(a.get("cash") or 0.0),
                     "status": a.get("status", "")}
        except Exception as e:  # noqa: BLE001
            logger.warning("balance fetch failed: %s", e)
            return {"balance": 0.0}

    def get_all_positions(self) -> list:
        """Normalized to the crypto shape so position_manager's
        reconcile_with_exchange works unchanged."""
        try:
            raw = _http("GET", f"{self.base_url}/v2/positions",
                         headers=self._headers) or []
        except Exception as e:  # noqa: BLE001
            logger.warning("positions fetch failed: %s", e)
            return []
        return [{"symbol": p.get("symbol"),
                  "positionAmt": float(p.get("qty") or 0),
                  "entryPrice": float(p.get("avg_entry_price") or 0),
                  "side": p.get("side", "long")}
                 for p in raw if float(p.get("qty") or 0) != 0]

    # ─── mutations (long/flat only) ──────────────────────────────────

    def open_long(self, symbol: str, quantity, sl_trigger_price=None,
                    tp_trigger_price=None) -> dict:
        qty = int(float(quantity))
        if qty <= 0:
            return {"ok": False, "error": "qty <= 0"}
        body = {"symbol": symbol.upper(), "qty": str(qty), "side": "buy",
                 "type": "market", "time_in_force": "day"}
        # Alpaca bracket orders are rejected outside regular hours; the
        # daily sleeves decide near the close, so the stop is managed by
        # the daemon rather than resting at the venue. That is recorded
        # on the position (bracket_kind) so the risk sentinel can see it.
        return self._mutate(
            f"BUY {qty} {symbol}",
            lambda: _http("POST", f"{self.base_url}/v2/orders",
                            headers=self._headers, body=body))

    def close_long_full(self, symbol: str) -> dict:
        return self._mutate(
            f"CLOSE {symbol}",
            lambda: _http("DELETE",
                            f"{self.base_url}/v2/positions/{symbol.upper()}",
                            headers=self._headers))

    def cancel_pending_orders(self, symbol: str) -> dict:
        return self._mutate(
            f"CANCEL orders {symbol}",
            lambda: _http("DELETE", f"{self.base_url}/v2/orders",
                            headers=self._headers))

    def open_short(self, *a, **kw):
        raise ShortingNotSupported(
            "Module 2 sleeves are long/flat. Reg SHO short-sale "
            "restrictions, locates and borrow fees are unmodeled.")

    def close_short_full(self, *a, **kw):
        raise ShortingNotSupported("long/flat only")

    def place_sl_order_short(self, *a, **kw):
        raise ShortingNotSupported("long/flat only")

    # ─── sizing ──────────────────────────────────────────────────────

    def get_qty_step(self, symbol: str = None) -> float:
        return 1.0          # whole shares; fractional complicates recon

    def get_min_qty(self, symbol: str = None) -> float:
        return 1.0

    def get_tick_size(self, symbol: str = None) -> float:
        return 0.01         # sub-$1 names quote finer; we trade ETFs

    def load_contract_info(self, symbols) -> None:
        return None         # no per-contract metadata needed for equities

    # ─── perp-only concepts ──────────────────────────────────────────

    def get_funding_rate(self, symbol: str = None):
        raise NotImplementedError(
            "funding rates are a perpetual-futures concept; equities have "
            "none. Returning 0 would silently satisfy a crypto filter.")
