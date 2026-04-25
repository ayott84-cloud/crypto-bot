"""Central configuration for the crypto trading bot.

All strategy parameters, system settings, and file paths live here.
No other file should contain magic numbers.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ─── Load .env file (keeps secrets out of shell history) ────────────────────
_BOT_DIR = Path(__file__).resolve().parent
load_dotenv(_BOT_DIR / ".env")          # crypto_bot/.env
load_dotenv(_BOT_DIR.parent / ".env")   # Crypto Trading/.env (fallback)

# ─── System Paths ────────────────────────────────────────────────────────────
# All runtime paths resolve relative to this file so the bot runs identically
# on Windows (local dev) and Linux (DigitalOcean droplet).
BOT_DIR = _BOT_DIR
PROJECT_DIR = BOT_DIR.parent
STATE_FILE = BOT_DIR / "state.json"
DASHBOARD_FILE = BOT_DIR / "dashboard.html"
LOG_FILE = BOT_DIR / "bot.log"

# Journal file: prefer the project root (where Excel naturally lives on the
# Windows machine) but fall back to inside the bot dir if the parent doesn't
# have one (typical on Linux deployments where the journal is generated fresh).
_JOURNAL_AT_PROJECT = PROJECT_DIR / "Trading_Journal.xlsx"
_JOURNAL_AT_BOT = BOT_DIR / "Trading_Journal.xlsx"
JOURNAL_FILE = _JOURNAL_AT_PROJECT if _JOURNAL_AT_PROJECT.exists() else _JOURNAL_AT_BOT

# Path to the bundled weex skill scripts. On Windows the canonical install
# location is the Claude skills dir; on Linux deployments we bundle a copy
# under crypto_bot/vendor/. executor.py handles the fallback chain.
WEEX_SKILL_DIR = BOT_DIR / "vendor"

# ─── Email Notifications ────────────────────────────────────────────────────
NOTIFY_ENABLED = os.getenv("NOTIFY_ENABLED", "true").lower() in ("true", "1", "yes")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "ayott84@gmail.com")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")       # your Gmail address
SMTP_PASS = os.getenv("SMTP_PASS", "")       # Gmail App Password (16 chars)

# ─── Trading Parameters ─────────────────────────────────────────────────────
INITIAL_CAPITAL = 5000.0          # WEEX account seed
MARGIN_PER_TRADE = 50.0           # USD margin per position
MAX_POSITIONS = 8                  # max simultaneous open positions (shared across bots)
DEFAULT_LEVERAGE = 10              # 10x leverage -> $500 notional per trade
DRY_RUN = True                     # master kill switch — set False for live
# Global trading enable flag. When False, both momentum and whale bots skip
# all new entries (existing positions still run their SL/TP).
# Set TRADING_ENABLED=false in .env to pause all new trades without editing code.
TRADING_ENABLED = os.getenv("TRADING_ENABLED", "true").lower() in ("true", "1", "yes")

# Backtest reference for yearly projection math
BACKTEST_YEARS = 5.3               # Dec 2020 → Apr 2026 window used in TradingView
BACKTEST_CAPITAL = 10000.0         # default Strategy Tester capital
BACKTEST_QTY_PCT = 10              # percent_of_equity default

# ─── Polling ─────────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 300       # check every 5 minutes
CANDLE_FETCH_COUNT = 300          # bars to fetch for indicator warmup
DASHBOARD_REGEN_CYCLES = 6       # regenerate dashboard every 6 cycles (~30 min)

# ─── Per-Asset Strategy Configurations ───────────────────────────────────────
ASSETS = {
    "BTC_1D": {
        # BTC Daily v2 — PF 2.741, WR 75%, 12 trades (~2.3/year), +$231 / +2.31%, DD 1.09%
        "symbol": "BTCUSDT",
        "interval": "1d",
        "ema_fast": 20,
        "ema_slow": 50,
        "close_above": "ema_fast",
        "atr_period": 14,
        "atr_sma_period": 20,
        "rsi_period": 14,
        "rsi_sma_period": 14,
        "rsi_min": 45,
        "rsi_max": 70,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "macd_mode": "strict",
        "use_pmo": True,
        "pmo_ema1": 35,
        "pmo_ema2": 20,
        "pmo_signal_len": 10,
        "use_volume_filter": False,
        "use_mfi_filter": True,
        "mfi_period": 14,
        "mfi_threshold": 50,
        # No BTC correlation filter — this IS BTC
        "use_btc_filter": False,
        "tp1_atr_mult": 1.5,
        "tp2_atr_mult": 3.5,
        "sl_atr_mult": 1.0,
        "tp1_close_pct": 0.50,
        "use_breakeven_after_tp1": True,
        "stale_bars": 5,        # ~5 days
        "stale_threshold_mult": 0.5,
        "strategy_name": "BTC Daily Momentum v2",
    },
    "ETH_1D": {
        # ETH Daily v2 — PF 2.754, WR 56.3%, 16 trades (~3/year), +$357 / +3.57%, DD 1.19%
        "symbol": "ETHUSDT",
        "interval": "1d",
        "ema_fast": 20,
        "ema_slow": 50,
        "close_above": "ema_fast",
        "atr_period": 14,
        "atr_sma_period": 20,
        "rsi_period": 14,
        "rsi_sma_period": 14,
        "rsi_min": 45,
        "rsi_max": 70,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "macd_mode": "strict",
        "use_pmo": True,
        "pmo_ema1": 35,
        "pmo_ema2": 20,
        "pmo_signal_len": 10,
        "use_volume_filter": False,
        "use_mfi_filter": True,
        "mfi_period": 14,
        "mfi_threshold": 50,
        "use_btc_filter": True,
        "btc_ema_period": 50,
        "tp1_atr_mult": 1.5,
        "tp2_atr_mult": 3.5,
        "sl_atr_mult": 1.0,
        "tp1_close_pct": 0.50,
        "use_breakeven_after_tp1": True,
        "stale_bars": 5,
        "stale_threshold_mult": 0.5,
        "strategy_name": "ETH Daily Momentum v2",
    },
    "BTC": {
        "symbol": "BTCUSDT",
        "interval": "4h",
        # Trend filter
        "ema_fast": 20,
        "ema_slow": 50,
        "close_above": "ema_fast",     # close > EMA 20
        # ATR regime
        "atr_period": 14,
        "atr_sma_period": 20,
        # RSI momentum
        "rsi_period": 14,
        "rsi_sma_period": 14,
        "rsi_min": 45,
        "rsi_max": 70,
        # MACD
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "macd_mode": "strict",         # histogram > 0
        # PMO
        "use_pmo": True,
        "pmo_ema1": 35,
        "pmo_ema2": 20,
        "pmo_signal_len": 10,
        # Volume (not used for BTC)
        "use_volume_filter": False,
        # NEW v2: MFI filter (volume-weighted RSI conviction)
        "use_mfi_filter": True,
        "mfi_period": 14,
        "mfi_threshold": 50,
        # NEW v2: ADX filter — disabled for v17 choice (higher trade count)
        # Enable with adx_threshold=20 for v18 behavior (PF 2.163 but fewer trades)
        "use_adx_filter": False,
        "adx_period": 14,
        "adx_threshold": 20,
        # Risk management
        "tp1_atr_mult": 1.5,
        "tp2_atr_mult": 3.5,
        "sl_atr_mult": 1.0,
        "tp1_close_pct": 0.50,        # close 50% at TP1
        # NEW v2: breakeven stop after TP1 (KEY IMPROVEMENT)
        "use_breakeven_after_tp1": True,
        "stale_bars": 8,
        "stale_threshold_mult": 0.5,   # < 50% of TP1 progress = stale
        # Meta
        "strategy_name": "BTC 4H Momentum v2",
    },
    "ETH": {
        "symbol": "ETHUSDT",
        "interval": "4h",
        "ema_fast": 20,
        "ema_slow": 50,
        "close_above": "ema_fast",
        "atr_period": 14,
        "atr_sma_period": 20,
        "rsi_period": 14,
        "rsi_sma_period": 14,
        "rsi_min": 45,
        "rsi_max": 70,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "macd_mode": "strict",
        "use_pmo": True,
        "pmo_ema1": 35,
        "pmo_ema2": 20,
        "pmo_signal_len": 10,
        "use_volume_filter": False,
        # v2: MFI volume-weighted RSI filter
        "use_mfi_filter": True,
        "mfi_period": 14,
        "mfi_threshold": 50,
        # v2: BTC correlation filter (only long ETH when BTC > EMA50 on 4H)
        "use_btc_filter": True,
        "btc_ema_period": 50,
        "tp1_atr_mult": 1.5,
        "tp2_atr_mult": 3.5,
        "sl_atr_mult": 1.0,
        "tp1_close_pct": 0.50,
        # v2: breakeven stop after TP1
        "use_breakeven_after_tp1": True,
        "stale_bars": 10,
        "stale_threshold_mult": 0.5,
        "strategy_name": "ETH 4H Momentum v2",
    },
    "XRP": {
        "symbol": "XRPUSDT",
        "interval": "1d",
        "ema_fast": 20,
        "ema_slow": 50,
        "close_above": "ema_slow",     # close > EMA 50 (different!)
        "atr_period": 14,
        "atr_sma_period": 20,
        "rsi_period": 14,
        "rsi_sma_period": 14,
        "rsi_min": 40,                 # wider RSI range
        "rsi_max": 70,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "macd_mode": "loose",          # hist > 0 OR macd_line > signal
        "use_pmo": False,              # PMO hurts XRP
        "use_volume_filter": True,     # volume confirmation
        "volume_sma_period": 20,
        "volume_threshold": 0.8,       # volume > 0.8 * SMA(volume, 20)
        # v2: BTC correlation filter (biggest boost for XRP — PF 2.66 -> 3.71)
        "use_btc_filter": True,
        "btc_ema_period": 50,
        "tp1_atr_mult": 1.5,
        "tp2_atr_mult": 3.5,
        "sl_atr_mult": 1.0,
        "tp1_close_pct": 0.50,
        # v2: breakeven stop after TP1
        "use_breakeven_after_tp1": True,
        "stale_bars": 3,              # XRP: cut fast
        "stale_threshold_mult": 0.5,
        "strategy_name": "XRP Daily Momentum v2",
    },
    "XRP_4H": {
        # Runs alongside XRP Daily — more frequent trades, lower PF
        # Backtest: PF 1.824, WR 57.8%, 45 trades, +$330 / +3.30%, DD 1.33%
        "symbol": "XRPUSDT",           # same underlying as XRP but different timeframe
        "interval": "4h",
        "ema_fast": 20,
        "ema_slow": 50,
        "close_above": "ema_slow",     # close > EMA 50 (XRP-style)
        "atr_period": 14,
        "atr_sma_period": 20,
        "rsi_period": 14,
        "rsi_sma_period": 14,
        "rsi_min": 45,                 # tighter than Daily for 4H noise
        "rsi_max": 65,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "macd_mode": "loose",
        "use_pmo": False,
        # MFI replaces simple volume filter (volume filter hurt 4H)
        "use_volume_filter": False,
        "use_mfi_filter": True,
        "mfi_period": 14,
        "mfi_threshold": 50,
        # BTC correlation filter
        "use_btc_filter": True,
        "btc_ema_period": 50,
        "tp1_atr_mult": 1.5,
        "tp2_atr_mult": 3.5,
        "sl_atr_mult": 1.0,
        "tp1_close_pct": 0.50,
        "use_breakeven_after_tp1": True,
        "stale_bars": 12,              # ~2 days of 4H bars
        "stale_threshold_mult": 0.5,
        "strategy_name": "XRP 4H Momentum v2",
    },
    "SOL": {
        "symbol": "SOLUSDT",
        "interval": "1d",
        # v2: PF 2.07, WR 58.3%, 12 trades, +1.92% P&L
        # Key insight: MFI outperforms simple volume SMA for SOL (volume info + RSI logic)
        "ema_fast": 20,
        "ema_slow": 50,
        "close_above": "ema_slow",     # close > EMA 50 (XRP-style)
        "atr_period": 14,
        "atr_sma_period": 20,
        "rsi_period": 14,
        "rsi_sma_period": 14,
        "rsi_min": 40,                 # wide RSI range
        "rsi_max": 70,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "macd_mode": "loose",          # hist > 0 OR macd_line > signal
        "use_pmo": False,              # no PMO
        # v2: MFI replaces simple volume filter (more selective for SOL)
        "use_volume_filter": False,
        "use_mfi_filter": True,
        "mfi_period": 14,
        "mfi_threshold": 50,
        # v2: BTC correlation filter (universal alt edge)
        "use_btc_filter": True,
        "btc_ema_period": 50,
        "tp1_atr_mult": 1.5,
        "tp2_atr_mult": 3.5,
        "sl_atr_mult": 1.0,
        "tp1_close_pct": 0.50,
        # v2: breakeven stop after TP1
        "use_breakeven_after_tp1": True,
        "stale_bars": 3,               # XRP-style fast cut
        "stale_threshold_mult": 0.5,
        "strategy_name": "SOL Daily Momentum v2",
    },
    # ═══ v2 Framework Momentum additions (Apr 2026 batch) ═══════════════════
    # All backtested on BINANCE Dec 2020 → Apr 2026 (5.3 yrs), PF >= 2.0
    "HBAR_4H": {
        "symbol": "HBARUSDT",
        "interval": "4h",
        "ema_fast": 20, "ema_slow": 50, "close_above": "ema_fast",
        "atr_period": 14, "atr_sma_period": 20,
        "rsi_period": 14, "rsi_sma_period": 14,
        "rsi_min": 50, "rsi_max": 65,
        "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "macd_mode": "strict",
        "use_pmo": True, "pmo_ema1": 35, "pmo_ema2": 20, "pmo_signal_len": 10,
        "use_volume_filter": False,
        "use_mfi_filter": False,
        "use_adx_filter": False,
        "use_btc_filter": True, "btc_ema_period": 50,
        "tp1_atr_mult": 1.5, "tp2_atr_mult": 3.5, "sl_atr_mult": 1.0,
        "tp1_close_pct": 0.50, "use_breakeven_after_tp1": True,
        "stale_bars": 24, "stale_threshold_mult": 0.5,
        "strategy_name": "HBAR 4H Momentum v2",
        "backtest_stats": {"pf": 2.364, "trades": 35, "pnl_pct": 3.2, "dd_pct": 1.1},
    },
    "HBAR_1D": {
        "symbol": "HBARUSDT",
        "interval": "1d",
        "ema_fast": 20, "ema_slow": 50, "close_above": "ema_fast",
        "atr_period": 14, "atr_sma_period": 20,
        "rsi_period": 14, "rsi_sma_period": 14,
        "rsi_min": 45, "rsi_max": 70,
        "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "macd_mode": "strict",
        "use_pmo": True, "pmo_ema1": 35, "pmo_ema2": 20, "pmo_signal_len": 10,
        "use_volume_filter": False,
        "use_mfi_filter": True, "mfi_period": 14, "mfi_threshold": 50,
        "use_adx_filter": True, "adx_period": 14, "adx_threshold": 20,
        "use_btc_filter": True, "btc_ema_period": 50,
        "tp1_atr_mult": 1.5, "tp2_atr_mult": 3.5, "sl_atr_mult": 1.0,
        "tp1_close_pct": 0.50, "use_breakeven_after_tp1": True,
        "stale_bars": 5, "stale_threshold_mult": 0.5,
        "strategy_name": "HBAR Daily Momentum v2",
        "backtest_stats": {"pf": 2.171, "trades": 14, "pnl_pct": 2.5, "dd_pct": 1.2},
    },
    "ADA_4H": {
        "symbol": "ADAUSDT",
        "interval": "4h",
        "ema_fast": 20, "ema_slow": 50, "close_above": "ema_fast",
        "atr_period": 14, "atr_sma_period": 20,
        "rsi_period": 14, "rsi_sma_period": 14,
        "rsi_min": 50, "rsi_max": 65,
        "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "macd_mode": "strict",
        "use_pmo": True, "pmo_ema1": 35, "pmo_ema2": 20, "pmo_signal_len": 10,
        "use_volume_filter": False,
        "use_mfi_filter": False,
        "use_adx_filter": False,
        "use_btc_filter": True, "btc_ema_period": 100,
        "tp1_atr_mult": 1.5, "tp2_atr_mult": 3.5, "sl_atr_mult": 0.8,
        "tp1_close_pct": 0.50, "use_breakeven_after_tp1": True,
        "stale_bars": 4, "stale_threshold_mult": 0.5,
        "strategy_name": "ADA 4H Momentum v2",
        "backtest_stats": {"pf": 2.285, "trades": 32, "pnl_pct": 2.74, "dd_pct": 0.56},
    },
    "ADA_1D": {
        "symbol": "ADAUSDT",
        "interval": "1d",
        "ema_fast": 20, "ema_slow": 50, "close_above": "ema_fast",
        "atr_period": 14, "atr_sma_period": 20,
        "rsi_period": 14, "rsi_sma_period": 14,
        "rsi_min": 45, "rsi_max": 70,
        "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "macd_mode": "strict",
        "use_pmo": False,
        "use_volume_filter": False,
        "use_mfi_filter": True, "mfi_period": 14, "mfi_threshold": 50,
        "use_adx_filter": False,
        "use_btc_filter": True, "btc_ema_period": 50,
        "tp1_atr_mult": 2.0, "tp2_atr_mult": 4.5, "sl_atr_mult": 1.0,
        "tp1_close_pct": 0.50, "use_breakeven_after_tp1": True,
        "stale_bars": 5, "stale_threshold_mult": 0.5,
        "strategy_name": "ADA Daily Momentum v2",
        "backtest_stats": {"pf": 2.243, "trades": 13, "pnl_pct": 2.8, "dd_pct": 1.0},
    },
    "DOT_4H": {
        "symbol": "DOTUSDT",
        "interval": "4h",
        "ema_fast": 20, "ema_slow": 50, "close_above": "ema_fast",
        "atr_period": 14, "atr_sma_period": 20,
        "rsi_period": 14, "rsi_sma_period": 14,
        "rsi_min": 55, "rsi_max": 65,
        "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "macd_mode": "strict",
        "use_pmo": False,
        "use_volume_filter": False,
        "use_mfi_filter": True, "mfi_period": 14, "mfi_threshold": 50,
        "use_adx_filter": True, "adx_period": 14, "adx_threshold": 21,
        "use_btc_filter": True, "btc_ema_period": 50,
        "tp1_atr_mult": 2.0, "tp2_atr_mult": 4.5, "sl_atr_mult": 1.0,
        "tp1_close_pct": 0.50, "use_breakeven_after_tp1": True,
        "stale_bars": 24, "stale_threshold_mult": 0.5,
        "strategy_name": "DOT 4H Momentum v2",
        "backtest_stats": {"pf": 2.009, "trades": 32, "pnl_pct": 3.47, "dd_pct": 1.00},
    },
    "DOT_1D": {
        "symbol": "DOTUSDT",
        "interval": "1d",
        "ema_fast": 20, "ema_slow": 50, "close_above": "ema_fast",
        "atr_period": 14, "atr_sma_period": 20,
        "rsi_period": 14, "rsi_sma_period": 14,
        "rsi_min": 50, "rsi_max": 70,
        "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "macd_mode": "strict",
        "use_pmo": True, "pmo_ema1": 35, "pmo_ema2": 20, "pmo_signal_len": 10,
        "use_volume_filter": False,
        "use_mfi_filter": True, "mfi_period": 14, "mfi_threshold": 55,
        "use_adx_filter": False,
        "use_btc_filter": True, "btc_ema_period": 200,
        "tp1_atr_mult": 2.0, "tp2_atr_mult": 4.5, "sl_atr_mult": 0.8,
        "tp1_close_pct": 0.50, "use_breakeven_after_tp1": True,
        "stale_bars": 4, "stale_threshold_mult": 0.5,
        "strategy_name": "DOT Daily Momentum v2",
        "backtest_stats": {"pf": 2.375, "trades": 13, "pnl_pct": 2.60, "dd_pct": 0.90},
    },
    "DOGE_4H": {
        "symbol": "DOGEUSDT",
        "interval": "4h",
        "ema_fast": 20, "ema_slow": 50, "close_above": "ema_fast",
        "atr_period": 14, "atr_sma_period": 20,
        "rsi_period": 14, "rsi_sma_period": 14,
        "rsi_min": 45, "rsi_max": 70,
        "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "macd_mode": "strict",
        "use_pmo": False,
        "use_volume_filter": False,
        "use_mfi_filter": False,
        "use_adx_filter": True, "adx_period": 14, "adx_threshold": 16,
        "use_btc_filter": True, "btc_ema_period": 50,
        "tp1_atr_mult": 2.0, "tp2_atr_mult": 4.5, "sl_atr_mult": 1.0,
        "tp1_close_pct": 0.50, "use_breakeven_after_tp1": True,
        "stale_bars": 12, "stale_threshold_mult": 0.5,
        "strategy_name": "DOGE 4H Momentum v2",
        "backtest_stats": {"pf": 2.003, "trades": 38, "pnl_pct": 3.0, "dd_pct": 1.3},
    },
    "DOGE_1D": {
        "symbol": "DOGEUSDT",
        "interval": "1d",
        "ema_fast": 20, "ema_slow": 50, "close_above": "ema_fast",
        "atr_period": 14, "atr_sma_period": 20,
        "rsi_period": 14, "rsi_sma_period": 14,
        "rsi_min": 45, "rsi_max": 70,
        "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "macd_mode": "strict",
        "use_pmo": True, "pmo_ema1": 35, "pmo_ema2": 20, "pmo_signal_len": 10,
        "use_volume_filter": False,
        "use_mfi_filter": True, "mfi_period": 14, "mfi_threshold": 50,
        "use_adx_filter": False,
        "use_btc_filter": True, "btc_ema_period": 50,
        "tp1_atr_mult": 1.5, "tp2_atr_mult": 3.5, "sl_atr_mult": 1.0,
        "tp1_close_pct": 0.50, "use_breakeven_after_tp1": True,
        "stale_bars": 5, "stale_threshold_mult": 0.5,
        "strategy_name": "DOGE Daily Momentum v2",
        "backtest_stats": {"pf": 3.167, "trades": 12, "pnl_pct": 4.5, "dd_pct": 1.0},
    },
    "SHIB_4H": {
        "symbol": "SHIBUSDT",
        "interval": "4h",
        "ema_fast": 20, "ema_slow": 50, "close_above": "ema_fast",
        "atr_period": 14, "atr_sma_period": 20,
        "rsi_period": 14, "rsi_sma_period": 14,
        "rsi_min": 45, "rsi_max": 70,
        "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "macd_mode": "strict",
        "use_pmo": False,
        "use_volume_filter": False,
        "use_mfi_filter": False,
        "use_adx_filter": True, "adx_period": 14, "adx_threshold": 16,
        "use_btc_filter": True, "btc_ema_period": 50,
        "tp1_atr_mult": 2.0, "tp2_atr_mult": 4.5, "sl_atr_mult": 1.0,
        "tp1_close_pct": 0.50, "use_breakeven_after_tp1": True,
        "stale_bars": 12, "stale_threshold_mult": 0.5,
        "strategy_name": "SHIB 4H Momentum v2",
        "backtest_stats": {"pf": 2.015, "trades": 36, "pnl_pct": 3.0, "dd_pct": 1.3},
    },
    "SHIB_1D": {
        "symbol": "SHIBUSDT",
        "interval": "1d",
        "ema_fast": 20, "ema_slow": 50, "close_above": "ema_fast",
        "atr_period": 14, "atr_sma_period": 20,
        "rsi_period": 14, "rsi_sma_period": 14,
        "rsi_min": 50, "rsi_max": 65,
        "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "macd_mode": "strict",
        "use_pmo": False,
        "use_volume_filter": False,
        "use_mfi_filter": False,
        "use_adx_filter": False,
        "use_btc_filter": True, "btc_ema_period": 100,
        "tp1_atr_mult": 2.0, "tp2_atr_mult": 4.5, "sl_atr_mult": 1.0,
        "tp1_close_pct": 0.50, "use_breakeven_after_tp1": True,
        "stale_bars": 5, "stale_threshold_mult": 0.5,
        "strategy_name": "SHIB Daily Momentum v2",
        "backtest_stats": {"pf": 5.495, "trades": 10, "pnl_pct": 3.63, "dd_pct": 0.83},
    },
}
