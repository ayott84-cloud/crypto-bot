"""One-shot script to regenerate dashboard with sample data for preview."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from dashboard import generate_dashboard

data = {
    "equity": 5037.80,
    "available": 4787.80,
    "unrealized_pnl": 18.30,
    "positions": [
        {"symbol": "BTCUSDT", "positionAmt": "0.005", "entryPrice": "84500.0",
         "markPrice": "85960.0", "leverage": "10", "unrealizedProfit": "7.30",
         "positionInitialMargin": "42.25", "liquidationPrice": "76050.0"},
        {"symbol": "ETHUSDT", "positionAmt": "0.312", "entryPrice": "1582.0",
         "markPrice": "1617.3", "leverage": "10", "unrealizedProfit": "11.01",
         "positionInitialMargin": "49.36", "liquidationPrice": "1423.8"},
    ],
    "funding_rates": [
        {"symbol": "DOGEUSDT", "lastFundingRate": "0.000310", "abs_rate": 0.00031, "annualized": 33.9, "direction": "SHORT"},
        {"symbol": "SUIUSDT", "lastFundingRate": "0.000250", "abs_rate": 0.00025, "annualized": 27.4, "direction": "SHORT"},
        {"symbol": "SOLUSDT", "lastFundingRate": "-0.000210", "abs_rate": 0.00021, "annualized": -23.0, "direction": "LONG"},
        {"symbol": "PEPEUSDT", "lastFundingRate": "-0.000180", "abs_rate": 0.00018, "annualized": -19.7, "direction": "LONG"},
        {"symbol": "BTCUSDT", "lastFundingRate": "0.000135", "abs_rate": 0.000135, "annualized": 14.8, "direction": "SHORT"},
        {"symbol": "LINKUSDT", "lastFundingRate": "0.000110", "abs_rate": 0.00011, "annualized": 12.0, "direction": "SHORT"},
        {"symbol": "ETHUSDT", "lastFundingRate": "0.000098", "abs_rate": 0.000098, "annualized": 10.7, "direction": "SHORT"},
        {"symbol": "AVAXUSDT", "lastFundingRate": "0.000075", "abs_rate": 0.000075, "annualized": 8.2, "direction": "SHORT"},
        {"symbol": "ADAUSDT", "lastFundingRate": "-0.000060", "abs_rate": 0.00006, "annualized": -6.6, "direction": "LONG"},
        {"symbol": "XRPUSDT", "lastFundingRate": "0.000050", "abs_rate": 0.00005, "annualized": 5.5, "direction": "SHORT"},
    ],
    "trades": [
        {"date_closed": "2026-04-03 08:00", "symbol": "BTCUSDT", "direction": "LONG", "entry_price": 81200, "exit_price": 82800, "quantity": 0.006, "leverage": 10, "net_pnl": 9.60, "strategy": "BTC 4H Momentum v13", "exit_reason": "TP1 Hit"},
        {"date_closed": "2026-04-04 16:00", "symbol": "ETHUSDT", "direction": "LONG", "entry_price": 1520, "exit_price": 1490, "quantity": 0.33, "leverage": 10, "net_pnl": -9.90, "strategy": "ETH 4H Momentum v14", "exit_reason": "SL Hit"},
        {"date_closed": "2026-04-05 12:00", "symbol": "XRPUSDT", "direction": "LONG", "entry_price": 2.05, "exit_price": 2.19, "quantity": 240, "leverage": 10, "net_pnl": 33.60, "strategy": "XRP Daily Momentum v14", "exit_reason": "TP2 Hit"},
        {"date_closed": "2026-04-06 08:00", "symbol": "BTCUSDT", "direction": "LONG", "entry_price": 82900, "exit_price": 83100, "quantity": 0.006, "leverage": 10, "net_pnl": 1.20, "strategy": "BTC 4H Momentum v13", "exit_reason": "Stale Exit"},
        {"date_closed": "2026-04-07 20:00", "symbol": "ETHUSDT", "direction": "LONG", "entry_price": 1545, "exit_price": 1602, "quantity": 0.32, "leverage": 10, "net_pnl": 18.24, "strategy": "ETH 4H Momentum v14", "exit_reason": "TP1 Hit"},
        {"date_closed": "2026-04-08 04:00", "symbol": "BTCUSDT", "direction": "LONG", "entry_price": 83500, "exit_price": 83150, "quantity": 0.006, "leverage": 10, "net_pnl": -2.10, "strategy": "BTC 4H Momentum v13", "exit_reason": "SL Hit"},
        {"date_closed": "2026-04-09 12:00", "symbol": "XRPUSDT", "direction": "LONG", "entry_price": 2.12, "exit_price": 2.08, "quantity": 235, "leverage": 10, "net_pnl": -9.40, "strategy": "XRP Daily Momentum v14", "exit_reason": "SL Hit"},
        {"date_closed": "2026-04-10 08:00", "symbol": "BTCUSDT", "direction": "LONG", "entry_price": 83800, "exit_price": 85400, "quantity": 0.006, "leverage": 10, "net_pnl": 9.60, "strategy": "BTC 4H Momentum v13", "exit_reason": "TP1 Hit"},
        {"date_closed": "2026-04-10 16:00", "symbol": "ETHUSDT", "direction": "LONG", "entry_price": 1570, "exit_price": 1610, "quantity": 0.318, "leverage": 10, "net_pnl": 12.72, "strategy": "ETH 4H Momentum v14", "exit_reason": "TP1 Hit"},
        {"date_closed": "2026-04-11 08:00", "symbol": "BTCUSDT", "direction": "LONG", "entry_price": 84200, "exit_price": 84900, "quantity": 0.003, "leverage": 10, "net_pnl": 2.10, "strategy": "BTC 4H Momentum v13", "exit_reason": "TP2 Hit"},
        {"date_closed": "2026-04-12 12:00", "symbol": "ETHUSDT", "direction": "LONG", "entry_price": 1595, "exit_price": 1580, "quantity": 0.314, "leverage": 10, "net_pnl": -4.71, "strategy": "ETH 4H Momentum v14", "exit_reason": "Stale Exit"},
        {"date_closed": "2026-04-13 04:00", "symbol": "BTCUSDT", "direction": "LONG", "entry_price": 84800, "exit_price": 85500, "quantity": 0.006, "leverage": 10, "net_pnl": 4.20, "strategy": "BTC 4H Momentum v13", "exit_reason": "TP1 Hit"},
    ],
    "recent_trades": [],
    "metrics": {
        "win_rate": 66.7, "profit_factor": 1.63, "avg_win": 11.41, "avg_loss": 6.53,
        "best_trade": 33.60, "worst_trade": -9.90, "max_drawdown": 2.8,
        "sharpe": 1.52, "expectancy": 5.43, "total_trades": 12,
    },
    "daily_pnl_labels": ["04-03","04-04","04-05","04-06","04-07","04-08","04-09","04-10","04-11","04-12","04-13"],
    "daily_pnl_values": [9.60, -9.90, 33.60, 1.20, 18.24, -2.10, -9.40, 22.32, 2.10, -4.71, 4.20],
    "equity_curve": [5000, 5009.60, 4999.70, 5033.30, 5034.50, 5052.74, 5050.64, 5041.24, 5063.56, 5065.66, 5060.95, 5065.15],
    "allocation": {"BTCUSDT": 42.25, "ETHUSDT": 49.36},
    "timestamp": "2026-04-13 19:35 UTC",
}
data["recent_trades"] = data["trades"]

generate_dashboard(data)
print("Dashboard regenerated with sample data")
