#!/usr/bin/env python3
"""
Hyperliquid Whale Scanner
Pulls leaderboard data + wallet positions from Hyperliquid's FREE public API.
No API keys needed.

Usage:
    python3 whale_scanner.py top          # Top 20 most profitable wallets + positions
    python3 whale_scanner.py rekt         # 20 worst-performing active wallets + positions
    python3 whale_scanner.py both         # Both side by side
    python3 whale_scanner.py wallet 0x... # Check a specific wallet

Output: JSON to stdout
"""

import json
import sys
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

HL_INFO = "https://api.hyperliquid.xyz/info"
HL_LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"


def parse_performances(window_performances):
    """Parse windowPerformances: list of [timeframe, {pnl, roi, vlm}] pairs."""
    result = {}
    if not window_performances:
        return result
    for item in window_performances:
        if isinstance(item, list) and len(item) == 2:
            timeframe, metrics = item
            result[timeframe] = metrics
    return result


def get_leaderboard():
    """Fetch the full Hyperliquid leaderboard (free, no key)."""
    resp = requests.get(HL_LEADERBOARD, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and "leaderboardRows" in data:
        return data["leaderboardRows"]
    elif isinstance(data, list):
        return data
    return []


def get_wallet_positions(address):
    """Fetch a wallet's open positions (free, no key)."""
    try:
        resp = requests.post(HL_INFO, json={
            "type": "clearinghouseState",
            "user": address
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        positions = []
        for pos in data.get("assetPositions", []):
            p = pos.get("position", pos)
            size = float(p.get("szi", 0))
            if size == 0:
                continue

            leverage_data = p.get("leverage", {})
            leverage = leverage_data.get("value", "?") if isinstance(leverage_data, dict) else str(leverage_data)
            liq_px = p.get("liquidationPx")

            positions.append({
                "coin": p.get("coin", "?"),
                "direction": "LONG" if size > 0 else "SHORT",
                "size": abs(size),
                "entry_price": float(p.get("entryPx", 0)),
                "unrealized_pnl": float(p.get("unrealizedPnl", 0)),
                "leverage": leverage,
                "liquidation_price": float(liq_px) if liq_px else None
            })

        return {
            "address": address,
            "positions": positions,
            "account_value": float(data.get("marginSummary", {}).get("accountValue", 0)),
            "has_positions": len(positions) > 0
        }
    except Exception as e:
        return {"address": address, "positions": [], "error": str(e), "has_positions": False}


def get_bulk_positions(addresses, max_workers=10):
    """Fetch positions for multiple wallets in parallel."""
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(get_wallet_positions, addr): addr for addr in addresses}
        for future in as_completed(futures):
            results.append(future.result())
    return results


def parse_entry(entry):
    """Parse a single leaderboard entry into clean format."""
    perfs = parse_performances(entry.get("windowPerformances", []))
    return {
        "address": entry.get("ethAddress", ""),
        "display_name": entry.get("displayName") or "Anonymous",
        "account_value": entry.get("accountValue", "0"),
        "pnl_alltime": perfs.get("allTime", {}).get("pnl", "0"),
        "roi_alltime": perfs.get("allTime", {}).get("roi", "0"),
        "pnl_month": perfs.get("month", {}).get("pnl", "0"),
        "pnl_week": perfs.get("week", {}).get("pnl", "0"),
        "pnl_day": perfs.get("day", {}).get("pnl", "0"),
    }


def analyze_group(wallets_with_positions, label):
    """Analyze what a group of wallets is doing — long vs short breakdown."""
    coin_longs = {}
    coin_shorts = {}
    total_positions = 0
    active_wallets = 0

    for w in wallets_with_positions:
        if w.get("has_positions"):
            active_wallets += 1
        for pos in w.get("positions", []):
            coin = pos["coin"]
            total_positions += 1
            if pos["direction"] == "LONG":
                coin_longs[coin] = coin_longs.get(coin, 0) + 1
            else:
                coin_shorts[coin] = coin_shorts.get(coin, 0) + 1

    all_coins = set(list(coin_longs.keys()) + list(coin_shorts.keys()))
    breakdown = []
    for coin in sorted(all_coins, key=lambda c: coin_longs.get(c, 0) + coin_shorts.get(c, 0), reverse=True):
        longs = coin_longs.get(coin, 0)
        shorts = coin_shorts.get(coin, 0)
        total = longs + shorts
        breakdown.append({
            "coin": coin,
            "longs": longs,
            "shorts": shorts,
            "total_traders": total,
            "long_pct": round(longs / total * 100) if total > 0 else 0,
            "short_pct": round(shorts / total * 100) if total > 0 else 0
        })

    return {
        "label": label,
        "wallets_scanned": len(wallets_with_positions),
        "wallets_with_open_positions": active_wallets,
        "total_open_positions": total_positions,
        "coin_breakdown": breakdown
    }


def scan_top(leaderboard, count=20):
    """Top traders: highest all-time PnL with active positions."""
    scan_count = min(count * 5, len(leaderboard))
    top_entries = leaderboard[:scan_count]

    top_addresses = [e.get("ethAddress", "") for e in top_entries if e.get("ethAddress")]
    print(f"Scanning top {len(top_addresses)} wallets for {count} with positions...", file=sys.stderr)
    top_positions = get_bulk_positions(top_addresses)

    pos_map = {w["address"]: w for w in top_positions}
    entry_map = {e.get("ethAddress"): e for e in top_entries}

    active = []
    for addr in top_addresses:
        w = pos_map.get(addr, {})
        if not w.get("has_positions"):
            continue
        info = parse_entry(entry_map.get(addr, {}))
        info["positions"] = w.get("positions", [])
        info["account_value_live"] = w.get("account_value", 0)
        active.append(info)
        if len(active) >= count:
            break

    return {
        "wallets": active,
        "analysis": analyze_group([pos_map.get(w["address"], {}) for w in active],
                                   f"Top {len(active)} Traders (best all-time PnL, with positions)"),
        "note": f"Scanned {len(top_addresses)} top wallets, found {len(active)} with open positions"
    }


def scan_rekt(leaderboard, count=20):
    """
    Rekt traders: wallets with the worst MONTHLY PnL that still have open positions.
    The bottom of the all-time leaderboard is blown-up accounts with nothing left.
    So instead, we sort by worst monthly PnL among wallets with real account value.
    """
    # Filter to wallets with real account value (> $1000)
    active_candidates = []
    for entry in leaderboard:
        try:
            acct_val = float(entry.get("accountValue", "0"))
        except (ValueError, TypeError):
            continue
        if acct_val < 1000:
            continue

        perfs = parse_performances(entry.get("windowPerformances", []))
        monthly_pnl = float(perfs.get("month", {}).get("pnl", "0"))
        active_candidates.append((monthly_pnl, entry))

    # Sort by worst monthly PnL (most negative first)
    active_candidates.sort(key=lambda x: x[0])

    # Take the worst ones and check for open positions
    scan_count = min(count * 5, len(active_candidates))
    worst = active_candidates[:scan_count]

    addresses = [e.get("ethAddress", "") for _, e in worst if e.get("ethAddress")]
    print(f"Scanning {len(addresses)} worst-performing active wallets for {count} with positions...", file=sys.stderr)
    rekt_positions = get_bulk_positions(addresses)

    pos_map = {w["address"]: w for w in rekt_positions}
    entry_map = {e.get("ethAddress"): e for _, e in worst}

    active = []
    for addr in addresses:
        w = pos_map.get(addr, {})
        if not w.get("has_positions"):
            continue
        info = parse_entry(entry_map.get(addr, {}))
        info["positions"] = w.get("positions", [])
        info["account_value_live"] = w.get("account_value", 0)
        active.append(info)
        if len(active) >= count:
            break

    return {
        "wallets": active,
        "analysis": analyze_group([pos_map.get(w["address"], {}) for w in active],
                                   f"Rekt Traders (worst monthly PnL, still active)"),
        "note": f"Scanned {len(addresses)} worst-month wallets (>$1k), found {len(active)} with open positions"
    }


def run_scan(mode="both", count=20):
    """Main scan."""
    print(f"Fetching Hyperliquid leaderboard...", file=sys.stderr)
    leaderboard = get_leaderboard()

    if not leaderboard:
        return {"error": "Could not fetch leaderboard data"}

    print(f"Found {len(leaderboard)} traders on leaderboard", file=sys.stderr)

    result = {}

    if mode in ("top", "both"):
        result["top_traders"] = scan_top(leaderboard, count)

    if mode in ("rekt", "both"):
        result["rekt_traders"] = scan_rekt(leaderboard, count)

    return result


def run_wallet(address):
    """Check a specific wallet."""
    return get_wallet_positions(address)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: whale_scanner.py [top|rekt|both|wallet <address>]", file=sys.stderr)
        sys.exit(1)

    mode = sys.argv[1].lower()

    if mode == "wallet":
        if len(sys.argv) < 3:
            print("Provide a wallet address", file=sys.stderr)
            sys.exit(1)
        output = run_wallet(sys.argv[2])
    elif mode in ("top", "rekt", "both"):
        count = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        output = run_scan(mode=mode, count=count)
    else:
        print(f"Unknown mode: {mode}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(output, indent=2))
