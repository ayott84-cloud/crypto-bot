"""One-shot WEEX account scanner.

Pulls all account data in a single comprehensive scan:
1. Account balance (USDT available, frozen, equity)
2. All open positions with detailed metrics
3. 30-day trade history
4. 30-day funding fee history
5. Top 40 funding rates
6. 24h stats for traded pairs
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

logger = logging.getLogger("crypto_bot.data_pull")

MS_PER_DAY = 86_400_000


def pull_all(executor) -> Dict[str, Any]:
    """Pull all account data and return as a structured dict."""
    now_ms = int(time.time() * 1000)
    thirty_days_ago_ms = now_ms - (30 * MS_PER_DAY)

    data: Dict[str, Any] = {}

    # 1. Account Balance
    logger.info("Pulling account balance...")
    data["balance"] = executor.get_account_balance()

    # 2. All Open Positions
    logger.info("Pulling open positions...")
    positions = executor.get_all_positions()
    for pos in positions:
        # Enrich with time since entry
        update_time = pos.get("updateTime", now_ms)
        if isinstance(update_time, str):
            update_time = int(update_time)
        elapsed_ms = now_ms - update_time
        pos["hours_since_entry"] = round(elapsed_ms / 3_600_000, 1)
    data["positions"] = positions

    # 3. Trade History (30 days) — paginate
    logger.info("Pulling 30-day trade history...")
    all_trades: List[dict] = []
    page_start = thirty_days_ago_ms
    for _ in range(20):  # max 20 pages
        trades = executor.get_trade_details(start_time=page_start, limit=100)
        if not trades:
            break
        all_trades.extend(trades)
        # Next page starts after last trade
        last_time = int(trades[-1].get("time", page_start))
        if last_time <= page_start:
            break
        page_start = last_time + 1
    data["trade_history"] = all_trades

    # Also get order history for SL/TP triggers
    logger.info("Pulling 30-day order history...")
    all_orders: List[dict] = []
    page_start = thirty_days_ago_ms
    for _ in range(20):
        orders = executor.get_order_history(start_time=page_start, limit=100)
        if not orders:
            break
        all_orders.extend(orders)
        last_time = int(orders[-1].get("updateTime", page_start))
        if last_time <= page_start:
            break
        page_start = last_time + 1
    data["order_history"] = all_orders

    # 4. Funding Fee History (30 days)
    logger.info("Pulling funding fee history...")
    funding_bills = executor.get_contract_bills(
        start_time=thirty_days_ago_ms,
        end_time=now_ms,
        income_type="position_funding"
    )
    data["funding_history"] = funding_bills

    # Aggregate by pair and day
    funding_by_pair: Dict[str, float] = {}
    funding_by_day: Dict[str, float] = {}
    for bill in funding_bills:
        symbol = bill.get("symbol", "UNKNOWN")
        income = float(bill.get("income", 0))
        timestamp = int(bill.get("time", 0))
        day = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

        funding_by_pair[symbol] = funding_by_pair.get(symbol, 0) + income
        funding_by_day[day] = funding_by_day.get(day, 0) + income

    data["funding_by_pair"] = funding_by_pair
    data["funding_by_day"] = dict(sorted(funding_by_day.items()))

    # 5. Current Funding Rates (all symbols, sorted by |rate|, top 40)
    logger.info("Pulling current funding rates...")
    all_rates = executor.get_funding_rate()
    # Sort by absolute funding rate, take top 40
    for r in all_rates:
        r["abs_rate"] = abs(float(r.get("lastFundingRate", "0")))
        rate = float(r.get("lastFundingRate", "0"))
        r["annualized_pct"] = rate * 3 * 365 * 100  # 3 funding periods/day
    all_rates.sort(key=lambda x: x["abs_rate"], reverse=True)
    data["funding_rates_top40"] = all_rates[:40]

    # 6. 24h Stats for Traded Pairs
    logger.info("Pulling 24h stats for traded pairs...")
    traded_symbols = set()
    for trade in all_trades:
        traded_symbols.add(trade.get("symbol", ""))
    for pos in positions:
        traded_symbols.add(pos.get("symbol", ""))
    traded_symbols.discard("")

    ticker_24h: List[dict] = []
    all_tickers = executor.get_ticker_24h()
    for t in all_tickers:
        if t.get("symbol") in traded_symbols:
            ticker_24h.append(t)
    data["ticker_24h"] = ticker_24h

    # Summary stats
    data["pull_timestamp"] = datetime.now(timezone.utc).isoformat()
    data["traded_pairs"] = list(traded_symbols)

    logger.info("Data pull complete: %d positions, %d trades, %d funding records, "
                "%d funding rates, %d 24h tickers",
                len(positions), len(all_trades), len(funding_bills),
                len(all_rates), len(ticker_24h))

    return data


def print_summary(data: Dict[str, Any]) -> None:
    """Print a human-readable summary of the pulled data."""
    print("\n" + "=" * 60)
    print("  WEEX ACCOUNT SCAN RESULTS")
    print("=" * 60)

    # Balance
    bal = data.get("balance", {})
    print(f"\n{'ACCOUNT BALANCE':─<40}")
    print(f"  Total Equity:     {bal.get('balance', 'N/A')} USDT")
    print(f"  Available:        {bal.get('availableBalance', 'N/A')} USDT")
    print(f"  Frozen Margin:    {bal.get('frozenMargin', 'N/A')} USDT")
    print(f"  Unrealized PnL:   {bal.get('unrealizePnl', 'N/A')} USDT")

    # Positions
    positions = data.get("positions", [])
    print(f"\n{'OPEN POSITIONS':─<40} ({len(positions)} total)")
    for p in positions:
        sym = p.get("symbol", "?")
        side = "LONG" if float(p.get("positionAmt", "0")) > 0 else "SHORT"
        entry = p.get("entryPrice", "?")
        mark = p.get("markPrice", "?")
        upnl = p.get("unrealizedProfit", "?")
        lev = p.get("leverage", "?")
        hrs = p.get("hours_since_entry", "?")
        print(f"  {sym:12s} {side:5s} entry={entry:>10s} mark={mark:>10s} "
              f"uPnL={upnl:>10s} lev={lev}x  ({hrs}h)")

    # Trade count
    trades = data.get("trade_history", [])
    print(f"\n{'TRADE HISTORY':─<40} ({len(trades)} fills in last 30 days)")

    # Funding
    funding = data.get("funding_by_pair", {})
    print(f"\n{'FUNDING FEES':─<40}")
    for pair, total in sorted(funding.items(), key=lambda x: abs(x[1]), reverse=True):
        print(f"  {pair:12s} {total:+.4f} USDT")

    # Top funding rates
    rates = data.get("funding_rates_top40", [])[:10]
    print(f"\n{'TOP 10 FUNDING RATES':─<40}")
    for r in rates:
        sym = r.get("symbol", "?")
        rate = float(r.get("lastFundingRate", 0))
        ann = r.get("annualized_pct", 0)
        direction = "SHORT" if rate > 0 else "LONG"
        print(f"  {sym:12s} rate={rate:+.6f} ({ann:+.1f}% APR) -> {direction}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__file__).rsplit("\\", 1)[0])
    from executor import Executor

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ex = Executor(dry_run=True)
    data = pull_all(ex)
    print_summary(data)
    print(f"\nFull JSON written to stdout ({len(json.dumps(data))} bytes)")
