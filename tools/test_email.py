#!/usr/bin/env python3
"""SMTP notifier test — sends two fake trade emails (open + close) to verify
notifications work. Runs the same code path the live bots use.

Usage on the droplet:
    cd /home/bot/crypto-bot
    venv/bin/python tools/test_email.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))

# Make sure .env is loaded (so SMTP creds are present)
from dotenv import load_dotenv
load_dotenv(BOT_DIR / ".env")

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from notifier import notify_trade_opened, notify_trade_closed


def main():
    print("=" * 60)
    print("  SMTP NOTIFIER TEST")
    print("=" * 60)
    print(f"  SMTP_USER:    {os.getenv('SMTP_USER', '(NOT SET)')}")
    print(f"  SMTP_PASS:    {'<set>' if os.getenv('SMTP_PASS') else '(NOT SET)'}")
    print(f"  NOTIFY_EMAIL: {os.getenv('NOTIFY_EMAIL', '(default: SMTP_USER)')}")
    print()

    # Test 1: TRADE OPENED (LONG)
    print("[1/3] Sending test 'TRADE OPENED LONG' email...")
    ok = notify_trade_opened(
        symbol="BTCUSDT",
        entry_price=78500.00,
        quantity="0.006",
        leverage=10,
        sl_price=77800.00,
        tp1_price=79500.00,
        tp2_price=80800.00,
        atr_at_entry=350.0,
        strategy="TEST — pre-deployment notifier check",
        entry_reason="EMA cross + RSI 55 + MACD strict + MFI 60",
        direction="LONG",
    )
    print(f"      Result: {'SENT OK' if ok else 'FAILED — see logs above'}")

    # Test 2: TRADE OPENED (SHORT — exercises the new direction-aware code path)
    print("\n[2/3] Sending test 'TRADE OPENED SHORT' email (whale-bot scenario)...")
    ok = notify_trade_opened(
        symbol="AVAXUSDT",
        entry_price=9.42,
        quantity="53.07",
        leverage=10,
        sl_price=9.95,    # SL above for SHORT
        tp1_price=8.36,   # TP below for SHORT
        tp2_price=8.36,
        atr_at_entry=0.35,
        strategy="Whale Track AVAX SHORT — TEST",
        entry_reason="CONSENSUS_SHORT 100% (8 wallets) | uPnL +$3.4M | funding confirms",
        direction="SHORT",
    )
    print(f"      Result: {'SENT OK' if ok else 'FAILED — see logs above'}")

    # Test 3: TRADE CLOSED (winner)
    print("\n[3/3] Sending test 'TRADE CLOSED' email (winner)...")
    ok = notify_trade_closed(
        symbol="BTCUSDT",
        direction="LONG",
        entry_price=78500.00,
        exit_price=79550.00,
        quantity=0.006,
        leverage=10,
        sl_price=77800.00,
        tp1_price=79500.00,
        tp2_price=80800.00,
        exit_reason="TP1 hit",
        strategy="TEST — pre-deployment notifier check",
        portfolio_value=5000.0,
    )
    print(f"      Result: {'SENT OK' if ok else 'FAILED — see logs above'}")

    print("\n" + "=" * 60)
    print("  Check your inbox at the configured NOTIFY_EMAIL.")
    print("  If FAILED above: check Gmail app password, check Gmail 'less secure")
    print("  app access' isn't blocking the new IP, check droplet outbound 587.")
    print("=" * 60)


if __name__ == "__main__":
    main()
