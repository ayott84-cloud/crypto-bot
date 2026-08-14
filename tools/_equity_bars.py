"""Module 2 Phase S1 — historical equity bars (Tiingo / Alpaca).

WHY THIS IS NOT tools/_binance_klines.py WITH A NEW BASE URL
------------------------------------------------------------
The crypto fetcher walks BACKWARD through date windows and stops when a
window comes back empty. That is a sound "end of history" test for a
24/7 market. For equities it is a bug with teeth: every weekend and
every holiday returns an empty window, so the chain would terminate at
the first Saturday and hand back a silently truncated series. That is
the same defect class that corrupted a crypto validation round in July
(and, once fixed, reversed the SOL_4H verdict outright).

Equities get a defense crypto never had: COMPLETENESS IS VERIFIABLE.
market_calendar knows exactly which sessions belong in [start, end], so
a hole is *detectable*, not merely suspected. Every fetch is checked
against the calendar and an incomplete series is announced loudly with
its missing dates — and flagged on the frame itself (`df.attrs`) so a
downstream replay can refuse to treat it as clean.

PROVIDERS
  tiingo  — daily, 30+ yr history, one ranged request (no chunk walk).
            Adjusted fields preferred; falls back to raw when a row
            omits them rather than dropping the bar.
  alpaca  — daily + intraday, cursor pagination (`next_page_token`),
            adjustment=all.

Stooq was in the original plan as a keyless cross-check and is NOT
implemented: as of Jul 31 2026 it serves a JavaScript bot-challenge
instead of CSV. Cross-validation is therefore Tiingo vs Alpaca — two
independent real APIs, which is a better check than a scraper anyway.

Module is BACKTEST-ONLY. Live trading routes through stock_executor.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

BOT_DIR = Path(__file__).resolve().parent.parent
import sys
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

import market_calendar as mc

logger = logging.getLogger("crypto_bot.tools._equity_bars")

TIINGO_BASE = "https://api.tiingo.com/tiingo/daily"
ALPACA_DATA_BASE = "https://data.alpaca.markets/v2/stocks"


def _load_env() -> None:
    """Load .env the way config.py does.

    Every crypto module gets dotenv loading as a side effect of
    importing config.py. The equity modules deliberately do NOT import
    config (so Module 3 can reuse them without dragging in the crypto
    universe), which means they must do it themselves — the first real
    droplet run reported "TIINGO_API_KEY not set" on a box where the key
    was sitting in .env.

    load_dotenv does not override variables already in the real
    environment, so a systemd EnvironmentFile still wins.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:      # dotenv absent: real env vars still work
        return
    try:
        load_dotenv(BOT_DIR / ".env")
        load_dotenv(BOT_DIR.parent / ".env")
    except OSError:
        pass


_load_env()

# Captured at import for test monkeypatching; the accessors below fall
# back to a live os.getenv so import ORDER can never decide whether a
# credential is visible.
TIINGO_API_KEY = os.getenv("TIINGO_API_KEY", "")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET", "")


def tiingo_key() -> str:
    return TIINGO_API_KEY or os.getenv("TIINGO_API_KEY", "")


def alpaca_keys() -> tuple:
    return (ALPACA_API_KEY or os.getenv("ALPACA_API_KEY", ""),
             ALPACA_API_SECRET or os.getenv("ALPACA_API_SECRET", ""))


def credential_status() -> dict:
    """Which credentials are visible — masked, never the values.

    Lets an operator tell 'not set' from 'set but not loaded' without
    ever echoing a secret into a terminal or a paste."""
    ak, asec = alpaca_keys()
    return {
        "TIINGO_API_KEY": "set" if tiingo_key() else "MISSING",
        "ALPACA_API_KEY": "set" if ak else "MISSING",
        "ALPACA_API_SECRET": "set" if asec else "MISSING",
    }

CACHE_DIR = BOT_DIR / "data" / "equity"

_RETRY_SLEEPS = (2.0, 5.0)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_TIMEOUT_S = 30.0

# Interior-hole tolerance. A vendor legitimately misses the odd session
# (an early-listing day, a symbol change); more than this is a defect.
_MAX_INTERIOR_MISSING = 0


class TransientFetchError(Exception):
    """Transport failure that persisted through every retry. Raised, never
    swallowed — an empty return would read as 'no data for this window'
    and silently shrink a backtest."""


class CredentialsMissing(Exception):
    """A provider was requested without its API key configured."""


class ApiRefused(Exception):
    """A non-retryable HTTP error, carrying the API's own explanation.

    403/401/404 mean the vendor is telling us something specific; the
    message is the fastest path to the fix and must not be swallowed.
    """


# ─── HTTP ─────────────────────────────────────────────────────────────────

def _http_get_json_once(url: str, headers: dict, params: dict):
    q = urllib.parse.urlencode({k: v for k, v in (params or {}).items()
                                  if v is not None})
    full = f"{url}?{q}" if q else url
    req = urllib.request.Request(full, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code in _RETRYABLE_STATUS:
            raise TransientFetchError(f"HTTP {e.code}") from e
        # Non-retryable: the API is REFUSING us and its body says why
        # ("you must accept the terms", "plan does not include this
        # endpoint", "invalid token"). Discarding it turns a one-line
        # answer into a guessing game — the same reason the Kalshi
        # schema-drift warning dumps field names.
        try:
            detail = e.read().decode("utf-8", "replace")[:400]
        except Exception:  # noqa: BLE001
            detail = "<no body>"
        raise ApiRefused(f"HTTP {e.code} from {url.split('?')[0]}: "
                          f"{detail}") from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
             ConnectionError) as e:
        raise TransientFetchError(str(e)) from e


def _http_get_json(url: str, headers: dict, params: dict):
    """GET with backoff on transient transport errors."""
    last = None
    for i, sleep_s in enumerate((0.0,) + _RETRY_SLEEPS):
        if sleep_s:
            logger.warning("equity-bars retry %d/%d in %.0fs: %s",
                             i, len(_RETRY_SLEEPS), sleep_s, last)
            time.sleep(sleep_s)
        try:
            return _http_get_json_once(url, headers, params)
        except TransientFetchError as e:
            last = e
    raise TransientFetchError(f"all retries exhausted: {last}")


# ─── Normalization ────────────────────────────────────────────────────────

_COLS = ["open", "high", "low", "close", "volume"]


def _frame(records) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=_COLS,
                             index=pd.DatetimeIndex([], name="date"))
    df = pd.DataFrame(records).set_index("date")
    df.index = pd.to_datetime(df.index, utc=True, format="mixed").tz_localize(None)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df[_COLS].astype(float)


def tiingo_rows_to_frame(rows) -> pd.DataFrame:
    """Tiingo daily JSON -> OHLCV frame. Adjusted values preferred; a row
    lacking them falls back to raw rather than being dropped (a dropped
    bar is an invisible hole)."""
    out = []
    for r in rows or []:
        try:
            out.append({
                "date":   str(r["date"])[:10],
                "open":   float(r.get("adjOpen", r.get("open"))),
                "high":   float(r.get("adjHigh", r.get("high"))),
                "low":    float(r.get("adjLow", r.get("low"))),
                "close":  float(r.get("adjClose", r.get("close"))),
                "volume": float(r.get("adjVolume", r.get("volume")) or 0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return _frame(out)


def alpaca_bars_to_frame(bars) -> pd.DataFrame:
    """Alpaca bar JSON -> OHLCV frame (request with adjustment=all)."""
    out = []
    for b in bars or []:
        try:
            out.append({
                "date":   str(b["t"])[:10],
                "open":   float(b["o"]), "high": float(b["h"]),
                "low":    float(b["l"]), "close": float(b["c"]),
                "volume": float(b.get("v") or 0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return _frame(out)


# ─── Completeness (the crypto fetcher could never do this) ───────────────

def completeness_report(symbol: str, df: pd.DataFrame,
                          start: date, end: date) -> dict:
    """Compare the fetched sessions against the calendar's truth.

    Absence BEFORE the first returned bar is not a defect — a symbol
    that listed mid-window has no earlier bars to give. Only INTERIOR
    holes count.

    Absence at the LIVE EDGE is the same kind of non-defect. An
    end-of-day vendor publishes a session's bar after that session
    closes, so today's bar is legitimately missing while the market is
    open and for some lag after. The live daemon polls continuously, so
    counting it produced a WARNING on every single cycle — and a warning
    that always fires is one nobody reads, which is how a real gap gets
    missed.

    Only TODAY is excused, and it is reported as `pending` rather than
    silently dropped. Excusing yesterday as well would hide a genuine
    one-day vendor outage, which is exactly what this check is for.
    """
    expected = [d for d in _daterange(start, end) if mc.is_trading_day(d)]
    got = set(pd.DatetimeIndex(df.index).date) if len(df) else set()
    today = date.today()
    pending = [d for d in expected if d >= today and d not in got]
    expected_settled = [d for d in expected if d < today]
    leading = 0
    if got:
        first = min(got)
        leading = sum(1 for d in expected_settled if d < first)
        interior = [d for d in expected_settled if d >= first and d not in got]
    else:
        interior = list(expected_settled)
    return {
        "symbol": symbol,
        "expected": len(expected),
        "got": len(got),
        "leading_absent": leading,
        "pending": len(pending),
        "missing": interior,
        "complete": len(interior) <= _MAX_INTERIOR_MISSING,
    }


def _daterange(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


# ─── Public fetch ─────────────────────────────────────────────────────────

def fetch_daily(symbol: str, start: date, end: date,
                  provider: str = "tiingo",
                  verify_complete: bool = True) -> pd.DataFrame:
    """Adjusted daily bars for [start, end] inclusive.

    Returns a DateTimeIndex frame. `df.attrs["incomplete"]` is True when
    interior sessions are missing — replays must refuse to treat such a
    frame as a clean window.
    """
    start, end = _as_date(start), _as_date(end)
    if provider == "tiingo":
        df = _fetch_tiingo_daily(symbol, start, end)
    elif provider == "alpaca":
        df = _fetch_alpaca_daily(symbol, start, end)
    else:
        raise ValueError(f"unknown provider {provider!r}")

    rep = completeness_report(symbol, df, start, end)
    df.attrs["incomplete"] = not rep["complete"]
    df.attrs["completeness"] = rep
    df.attrs["provider"] = provider
    df.attrs["calendar_backend"] = mc.backend_note()
    if verify_complete and not rep["complete"]:
        sample = ", ".join(d.isoformat() for d in rep["missing"][:5])
        more = f" (+{len(rep['missing']) - 5} more)" if len(rep["missing"]) > 5 else ""
        msg = (f"INCOMPLETE {symbol} {provider}: {rep['got']} of "
                f"{rep['expected']} sessions — {len(rep['missing'])} interior "
                f"gaps: {sample}{more}")
        logger.warning(msg)
        print(f"  WARN [{symbol}]: {msg}")
    return df


def _fetch_tiingo_daily(symbol: str, start: date, end: date) -> pd.DataFrame:
    key = tiingo_key()
    if not key:
        raise CredentialsMissing(
            "TIINGO_API_KEY not visible to this process. It is read from "
            f"the real environment or from {BOT_DIR / '.env'} / "
            f"{BOT_DIR.parent / '.env'}. Verify without printing the key:\n"
            "  grep -c '^TIINGO_API_KEY=' .env      (expect 1)\n"
            "  venv/bin/python -c \"import sys; sys.path.insert(0,'.'); "
            "from tools._equity_bars import credential_status; "
            "print(credential_status())\"")
    rows = _http_get_json(
        f"{TIINGO_BASE}/{symbol.lower()}/prices",
        headers={"Content-Type": "application/json",
                  "Authorization": f"Token {key}"},
        params={"startDate": start.isoformat(), "endDate": end.isoformat(),
                 "format": "json", "resampleFreq": "daily"})
    return tiingo_rows_to_frame(rows)


def _fetch_alpaca_daily(symbol: str, start: date, end: date) -> pd.DataFrame:
    ak, asec = alpaca_keys()
    if not (ak and asec):
        raise CredentialsMissing(
            "ALPACA_API_KEY / ALPACA_API_SECRET not visible to this "
            f"process (checked the environment and {BOT_DIR / '.env'}). "
            "Paper keys are instant at alpaca.markets and need no funding.")
    headers = {"APCA-API-KEY-ID": ak, "APCA-API-SECRET-KEY": asec}
    frames, token, guard = [], None, 0
    while True:
        guard += 1
        if guard > 500:                       # cursor-loop backstop
            raise TransientFetchError(f"{symbol}: pagination exceeded 500 pages")
        payload = _http_get_json(
            f"{ALPACA_DATA_BASE}/{symbol.upper()}/bars",
            headers=headers,
            params={"timeframe": "1Day", "adjustment": "all", "limit": 10000,
                     "start": start.isoformat(), "end": end.isoformat(),
                     "page_token": token})
        frames.append(alpaca_bars_to_frame((payload or {}).get("bars")))
        token = (payload or {}).get("next_page_token")
        # NOTE: termination is the CURSOR going None — never "this page
        # was empty". A holiday-heavy page can legitimately be short.
        if not token:
            break
    if not frames:
        return _frame([])
    df = pd.concat(frames)
    return df[~df.index.duplicated(keep="last")].sort_index()


# ─── Interop with the existing replay harness ────────────────────────────

def to_positional_rows(df: pd.DataFrame, interval: str = "1d") -> list:
    """OHLCV frame -> WEEX 11-column positional rows.

    signals.build_dataframe reads df["close_time"] unconditionally, so
    the 11-column shape is mandatory. close_time is the SESSION CLOSE
    (16:00 ET, or 13:00 on a half day) — stamping a daily bar at
    midnight would place its close 17 hours before the market actually
    shut and corrupt any as-of higher-timeframe slice.
    """
    rows = []
    for ts, r in df.iterrows():
        d = ts.date() if hasattr(ts, "date") else ts
        open_ms = int(pd.Timestamp(d).tz_localize("UTC").timestamp() * 1000)
        if interval == "1d" and mc.is_trading_day(d):
            _o, close_dt = mc.session_bounds(d)
            close_ms = int(close_dt.astimezone(timezone.utc).timestamp() * 1000)
        else:
            close_ms = open_ms + _interval_ms(interval) - 1
        rows.append([
            open_ms, str(r["open"]), str(r["high"]), str(r["low"]),
            str(r["close"]), str(r["volume"]),
            close_ms, "0", "0", "0", "0",
        ])
    return rows


def _interval_ms(interval: str) -> int:
    minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60,
                "1d": 1440}.get(str(interval).lower())
    if minutes is None:
        raise ValueError(f"unsupported equity interval {interval!r}")
    return minutes * 60_000


# ─── Cross-vendor validation ─────────────────────────────────────────────

def cross_check(a: pd.DataFrame, b: pd.DataFrame,
                  tol_pct: float = 0.5) -> dict:
    """Compare two vendors' closes on their SHARED dates.

    Split/dividend adjustment errors are the #1 silent backtest killer:
    they do not raise, they just quietly rescale part of a price series.
    Two independent vendors disagreeing by more than rounding noise is
    the only cheap way to catch one.
    """
    shared = a.index.intersection(b.index)
    if len(shared) == 0:
        return {"agree": False, "compared": 0, "max_divergence_pct": 0.0,
                 "worst_date": None, "note": "no shared dates"}
    ca, cb = a.loc[shared, "close"], b.loc[shared, "close"]
    denom = ca.abs().replace(0.0, float("nan"))
    div = ((ca - cb).abs() / denom * 100.0).dropna()
    if div.empty:
        return {"agree": False, "compared": 0, "max_divergence_pct": 0.0,
                 "worst_date": None, "note": "no comparable closes"}
    worst_idx = div.idxmax()
    worst = float(div.loc[worst_idx])
    return {
        "agree": worst <= tol_pct,
        "compared": int(len(div)),
        "max_divergence_pct": round(worst, 4),
        "worst_date": worst_idx.date(),
        "median_divergence_pct": round(float(div.median()), 4),
    }


# ─── Parquet cache ────────────────────────────────────────────────────────

def cache_path(symbol: str, interval: str = "1d",
                 provider: str = "tiingo", suffix: str = ".parquet") -> Path:
    return CACHE_DIR / provider / interval / f"{symbol.upper()}{suffix}"


def save_cache(df: pd.DataFrame, symbol: str, interval: str = "1d",
                 provider: str = "tiingo") -> Path:
    """Cache bars, preferring Parquet but never REQUIRING it.

    The droplet has neither pyarrow nor fastparquet, and adding ~100MB
    of dependency to a 1GB box for a convenience cache is the wrong
    trade. Gzipped CSV holds ~6,000 daily rows in a few dozen KB.
    """
    p = cache_path(symbol, interval, provider)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(p)
        return p
    except (ImportError, ValueError):
        csv_p = cache_path(symbol, interval, provider, ".csv.gz")
        df.to_csv(csv_p, compression="gzip")
        if p.exists():          # drop a stale parquet so loads can't split
            try:
                p.unlink()
            except OSError:
                pass
        return csv_p


def load_cache(symbol: str, interval: str = "1d",
                 provider: str = "tiingo") -> Optional[pd.DataFrame]:
    """Load cached bars and RESTORE the completeness verdict.

    df.attrs survives no serializer, so a cached frame would come back
    from disk looking clean even when the fetch had found interior
    holes — turning a loud defect back into a silent one, which is the
    failure class this module exists to prevent. Rather than persist the
    verdict (which could then go stale), it is recomputed from the
    calendar over the frame's own span: cheap and always current.
    """
    df = None
    for suffix in (".parquet", ".csv.gz"):
        p = cache_path(symbol, interval, provider, suffix)
        if not p.exists():
            continue
        try:
            if suffix == ".parquet":
                df = pd.read_parquet(p)
            else:
                df = pd.read_csv(p, index_col=0, parse_dates=[0],
                                   compression="gzip")
            break
        except Exception:  # noqa: BLE001
            continue
    if df is None or len(df) == 0:
        return df if df is not None else None

    df.index = pd.DatetimeIndex(df.index)
    df = df.sort_index()
    rep = completeness_report(symbol, df, df.index[0].date(),
                                df.index[-1].date())
    df.attrs["incomplete"] = not rep["complete"]
    df.attrs["completeness"] = rep
    df.attrs["provider"] = provider
    df.attrs["from_cache"] = True
    return df


def _as_date(d) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d)[:10])
