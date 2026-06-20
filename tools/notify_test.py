"""Test email harness — send one synthetic OPEN + CLOSE per bot.

Verifies each bot's email pipeline end-to-end (SMTP creds, network,
notifier rendering). Each email's subject is prefixed with [TEST] so
they're easy to filter out of your inbox if you don't want them
archived alongside real trades.

Usage:
  python tools/notify_test.py              # all bots
  python tools/notify_test.py --bot whale  # one bot

Bots covered:
  momentum  — replays the LONG-trade flow from main.py
  whale     — replays a SHORT consensus entry/exit
  funding   — replays a LONG fade entry
  breakout  — replays a Donchian-55 LONG breakout
  pair      — replays an ETH/BTC z-score reversal
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("notify_test")


def _send_open_close(bot: str, *, symbol: str, direction: str,
                       entry: float, exit_p: float, qty: float,
                       leverage: int, sl: float, tp1: float, tp2: float,
                       atr: float, strategy: str, entry_reason: str,
                       exit_reason: str) -> bool:
    """Send the synthetic OPEN then CLOSE pair for `bot`."""
    from notifier import notify_trade_opened, notify_trade_closed

    # Prefix strategy + reasons with [TEST] for easy inbox filtering
    test_strategy = f"[TEST] {strategy}"
    print(f"  → OPEN  {bot:9s} {symbol} {direction} @ {entry}")
    ok_open = notify_trade_opened(
        symbol=f"[TEST-{bot.upper()}] {symbol}",
        entry_price=entry,
        quantity=str(qty),
        leverage=leverage,
        sl_price=sl,
        tp1_price=tp1,
        tp2_price=tp2,
        atr_at_entry=atr,
        strategy=test_strategy,
        entry_reason=f"[TEST] {entry_reason}",
        direction=direction,
    )
    print(f"  → CLOSE {bot:9s} {symbol} {direction} @ {exit_p}")
    ok_close = notify_trade_closed(
        symbol=f"[TEST-{bot.upper()}] {symbol}",
        direction=direction,
        entry_price=entry,
        exit_price=exit_p,
        quantity=qty,
        leverage=leverage,
        sl_price=sl,
        tp1_price=tp1,
        tp2_price=tp2,
        exit_reason=f"[TEST] {exit_reason}",
        strategy=test_strategy,
        portfolio_value=5000.0,
    )
    return bool(ok_open and ok_close)


def test_momentum() -> bool:
    return _send_open_close(
        "momentum",
        symbol="BTCUSDT",
        direction="LONG",
        entry=85_000.0, exit_p=86_500.0, qty=0.005, leverage=10,
        sl=83_500.0, tp1=86_500.0, tp2=88_000.0,
        atr=850.0,
        strategy="BTC 1D Momentum v2",
        entry_reason="EMA20>EMA50, RSI cross 55, MACD>0, ATR>SMA",
        exit_reason="TP1 Hit",
    )


def test_whale() -> bool:
    return _send_open_close(
        "whale",
        symbol="SOLUSDT",
        direction="SHORT",
        entry=185.50, exit_p=180.25, qty=2.7, leverage=10,
        sl=189.20, tp1=181.80, tp2=178.10,
        atr=2.20,
        strategy="Whale Track SOL SHORT",
        entry_reason="smart short 87% (12 wallets), net -$3.2M, rekt long 78%",
        exit_reason="TP1 Hit",
    )


def test_funding() -> bool:
    return _send_open_close(
        "funding",
        symbol="HOMEUSDT",
        direction="LONG",
        entry=0.00125, exit_p=0.00132, qty=200_000, leverage=10,
        sl=0.00118, tp1=0.00132, tp2=0.00140,
        atr=0.00005,
        strategy="Funding Fade",
        entry_reason="funding rate -2.4% / 8h, percentile 99",
        exit_reason="Funding reverted",
    )


def test_breakout() -> bool:
    return _send_open_close(
        "breakout",
        symbol="ETHUSDT",
        direction="LONG",
        entry=3_280.0, exit_p=3_410.0, qty=0.076, leverage=10,
        sl=3_180.0, tp1=3_380.0, tp2=3_480.0,
        atr=40.0,
        strategy="ETH 4H Breakout (Turtle 55/20)",
        entry_reason="Donchian-55 upper break + volume 1.8x SMA + 1D EMA20>EMA50",
        exit_reason="Donchian Exit (20-bar low)",
    )


def test_pair() -> bool:
    return _send_open_close(
        "pair",
        symbol="ETH/BTC pair",
        direction="LONG",
        entry=0.04250, exit_p=0.04425, qty=0.24, leverage=10,
        sl=0.04463, tp1=0.04376, tp2=0.04500,
        atr=0.0,
        strategy="Pair ETH/BTC z-score reversion",
        entry_reason="z = -2.31, ratio 0.04250 (long ETH leg / short BTC leg)",
        exit_reason="Z Reverted",
    )


def test_scalp() -> bool:
    return _send_open_close(
        "scalp",
        symbol="BTCUSDT",
        direction="LONG",
        entry=68_500.0, exit_p=70_555.0, qty=0.014, leverage=10,
        sl=67_472.5, tp1=70_555.0, tp2=70_555.0,
        atr=180.0,
        strategy="BTC 5m Scalp",
        entry_reason="Vol-expansion + 20-bar new high LONG",
        exit_reason="TP Hit",
    )


_BOTS = {
    "momentum": test_momentum,
    "whale":    test_whale,
    "funding":  test_funding,
    "breakout": test_breakout,
    "pair":     test_pair,
    "scalp":    test_scalp,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--bot", choices=list(_BOTS) + ["all"], default="all")
    args = parser.parse_args()

    # Probe config first — at least ONE channel must be configured
    try:
        from config import NOTIFY_ENABLED, SMTP_HOST, SMTP_USER, NOTIFY_EMAIL
    except ImportError as e:
        print(f"Config import failed: {e}", file=sys.stderr)
        sys.exit(1)
    try:
        from config import DISCORD_WEBHOOK_URL
    except ImportError:
        DISCORD_WEBHOOK_URL = ""

    smtp_ok    = bool(SMTP_USER and NOTIFY_EMAIL)
    discord_ok = bool(DISCORD_WEBHOOK_URL)

    print(f"NOTIFY_ENABLED       = {NOTIFY_ENABLED}")
    print(f"SMTP_HOST            = {SMTP_HOST}")
    print(f"SMTP_USER            = {'(set)' if SMTP_USER else '(MISSING)'}")
    print(f"NOTIFY_EMAIL         = {NOTIFY_EMAIL or '(MISSING)'}")
    print(f"DISCORD_WEBHOOK_URL  = {'(set)' if discord_ok else '(MISSING)'}")
    if not NOTIFY_ENABLED:
        print("\nWARN: NOTIFY_ENABLED is false — nothing will send.")
        sys.exit(1)
    if not (smtp_ok or discord_ok):
        print("\nERROR: configure at least one of SMTP_USER+NOTIFY_EMAIL "
              "or DISCORD_WEBHOOK_URL in .env.")
        sys.exit(1)
    if smtp_ok and not discord_ok:
        print("\nNote: SMTP configured, Discord not. If SMTP is blocked by "
              "the cloud provider, set DISCORD_WEBHOOK_URL for HTTPS delivery.")
    if discord_ok and not smtp_ok:
        print("\nNote: Discord configured, SMTP not. All notifications will "
              "go to Discord only.")

    bots = list(_BOTS) if args.bot == "all" else [args.bot]
    print(f"\nSending {len(bots) * 2} test emails ({len(bots)} bots × OPEN + CLOSE):\n")

    results = {}
    for bot in bots:
        try:
            ok = _BOTS[bot]()
            results[bot] = ok
        except Exception as e:
            logger.exception("test for %s raised: %s", bot, e)
            results[bot] = False

    print(f"\n=== Results ===")
    for bot, ok in results.items():
        print(f"  {bot:9s} {'OK' if ok else 'FAIL'}")
    n_ok = sum(1 for v in results.values() if v)
    print(f"\n{n_ok}/{len(results)} bots succeeded. Check your inbox for "
          f"{n_ok * 2} emails (all subject lines start with [TEST-{{BOT}}]).")
    sys.exit(0 if n_ok == len(results) else 1)


if __name__ == "__main__":
    main()
