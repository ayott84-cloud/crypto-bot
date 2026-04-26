#!/usr/bin/env python3
"""WEEX API diagnostic — dumps raw responses so we can see what the API actually returns.

Investigates the issue where get_contract_info returns 0 symbols and
get_symbol_price returns None on the droplet, while account.get_account_balance
authenticates successfully.

Usage on the droplet:
    cd /home/bot/crypto-bot
    venv/bin/python tools/diagnose_weex.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Make the bot dir importable so config + executor work
BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))

# Load .env so WEEX creds are present when running outside systemd
from dotenv import load_dotenv  # noqa: E402
load_dotenv(BOT_DIR / ".env")

from executor import Executor  # noqa: E402


def _trim(s: str, n: int = 1500) -> str:
    return s if len(s) <= n else s[:n] + f"\n  ... [truncated, total {len(s)} chars]"


def main():
    print("=" * 70)
    print("  WEEX API DIAGNOSTIC")
    print("=" * 70)

    # Show creds presence (NOT values)
    print(f"\n  WEEX_API_KEY:        {'<set, ' + str(len(os.getenv('WEEX_API_KEY', ''))) + ' chars>' if os.getenv('WEEX_API_KEY') else '(NOT SET)'}")
    print(f"  WEEX_API_SECRET:     {'<set, ' + str(len(os.getenv('WEEX_API_SECRET', ''))) + ' chars>' if os.getenv('WEEX_API_SECRET') else '(NOT SET)'}")
    print(f"  WEEX_API_PASSPHRASE: {'<set>' if os.getenv('WEEX_API_PASSPHRASE') else '(NOT SET)'}")

    ex = Executor(dry_run=True)

    # ─── Test 1: authenticated endpoint (control case — should work) ───
    print("\n" + "─" * 70)
    print("  [1] account.get_account_balance (AUTH required — control case)")
    print("─" * 70)
    try:
        result = ex._call("account.get_account_balance")
        print(_trim(json.dumps(result, indent=2, default=str), 1200))
    except Exception as e:
        print(f"EXCEPTION: {type(e).__name__}: {e}")

    # ─── Test 2: public market endpoint that's failing ───
    print("\n" + "─" * 70)
    print("  [2] market.get_contract_info (PUBLIC — currently returns 0 symbols)")
    print("─" * 70)
    try:
        result = ex._call("market.get_contract_info")
        # Print structural breakdown
        print(f"  result.ok:       {result.get('ok')}")
        print(f"  result.status:   {result.get('status')}")
        if result.get('error'):
            print(f"  result.error:    {json.dumps(result['error'], default=str)}")
        data = result.get('data')
        print(f"  result.data:     {type(data).__name__}")
        if isinstance(data, dict):
            print(f"  data top-level keys: {list(data.keys())}")
            inner = data.get('data')
            if isinstance(inner, dict):
                print(f"  data['data'] keys: {list(inner.keys())}")
                syms = inner.get('symbols')
                if isinstance(syms, list):
                    print(f"  data['data']['symbols']: list of {len(syms)} items")
                    if syms:
                        print(f"  first symbol example:")
                        print(_trim(json.dumps(syms[0], indent=4, default=str), 600))
                else:
                    print(f"  data['data']['symbols']: NOT a list (got {type(syms).__name__})")
            elif isinstance(inner, list):
                print(f"  data['data']: list of {len(inner)} items")
                if inner:
                    print(f"  first item: {_trim(json.dumps(inner[0], indent=4, default=str), 400)}")
            else:
                print(f"  data['data']: {type(inner).__name__} = {_trim(repr(inner), 400)}")
        # Always print the raw response for offline analysis
        print("\n  ── Full raw response ──")
        print(_trim(json.dumps(result, indent=2, default=str), 2500))
    except Exception as e:
        print(f"EXCEPTION: {type(e).__name__}: {e}")

    # ─── Test 3: symbol price fetch (also failing) ───
    for test_sym in ("BTCUSDT", "AVAXUSDT"):
        print("\n" + "─" * 70)
        print(f"  [3] market.get_symbol_price symbol={test_sym} (PUBLIC)")
        print("─" * 70)
        try:
            result = ex._call("market.get_symbol_price", query={"symbol": test_sym})
            print(f"  result.ok:     {result.get('ok')}")
            print(f"  result.status: {result.get('status')}")
            if result.get('error'):
                print(f"  result.error:  {json.dumps(result['error'], default=str)}")
            print("\n  ── Full raw response ──")
            print(_trim(json.dumps(result, indent=2, default=str), 1500))
            # What our executor's parser does:
            parsed = ex.get_symbol_price(test_sym)
            print(f"\n  executor.get_symbol_price() returned: {parsed}")
        except Exception as e:
            print(f"EXCEPTION: {type(e).__name__}: {e}")

    # ─── Test 4: ticker24h — different public endpoint, sometimes more reliable ───
    print("\n" + "─" * 70)
    print("  [4] market.get_ticker24h symbol=BTCUSDT (alternate public endpoint)")
    print("─" * 70)
    try:
        result = ex._call("market.get_ticker24h", query={"symbol": "BTCUSDT"})
        print(_trim(json.dumps(result, indent=2, default=str), 1500))
    except Exception as e:
        print(f"EXCEPTION: {type(e).__name__}: {e}")

    print("\n" + "=" * 70)
    print("  DIAGNOSTIC COMPLETE — paste the output back to Claude")
    print("=" * 70)


if __name__ == "__main__":
    main()
