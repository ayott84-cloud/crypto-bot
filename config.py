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

# Journal: JSONL append-only file inside the bot dir. Replaced the Excel
# journal in Apr 2026; the dashboard's Export Trades modal covers the CSV
# use case directly. See journal.py for the schema.
JOURNAL_FILE = BOT_DIR / "trades.jsonl"

# Path to the bundled weex skill scripts. On Windows the canonical install
# location is the Claude skills dir; on Linux deployments we bundle a copy
# under crypto_bot/vendor/. executor.py handles the fallback chain.
WEEX_SKILL_DIR = BOT_DIR / "vendor"

# ─── Email Notifications ────────────────────────────────────────────────────
NOTIFY_ENABLED = os.getenv("NOTIFY_ENABLED", "true").lower() in ("true", "1", "yes")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "ayott84@gmail.com")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
# Default to SMTPS (465) instead of submission+STARTTLS (587). Many cloud
# providers (DigitalOcean included) block outbound 587 by default but allow
# 465. The notifier handles both: 465 uses SMTP_SSL, 587 uses STARTTLS.
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")       # your Gmail address
SMTP_PASS = os.getenv("SMTP_PASS", "")       # Gmail App Password (16 chars)

# ─── Discord webhook (HTTPS) — fallback when SMTP is blocked ────────────────
# DigitalOcean blocks outbound SMTP by default; Discord webhooks work over
# HTTPS so they bypass the SMTP block entirely. Create a webhook in any
# Discord channel (Server Settings → Integrations → Webhooks → New Webhook),
# copy the URL, and set DISCORD_WEBHOOK_URL in .env. The notifier fires both
# email AND Discord if both are configured; either one alone if only one is
# configured.
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

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

# P3.6 — BTC-ETH 30d rolling-returns correlation gate (fleet-level).
# When ON, momentum alt entries are blocked while correlation < min:
# correlation breakdown precedes rotational chop where trend signals bleed
# (practitioner research, Jul 2026 sweep: PF roughly doubled with gate).
# Default OFF until validated through the honest replay pipeline (P4).
USE_BTC_ETH_CORR_GATE = os.getenv(
    "USE_BTC_ETH_CORR_GATE", "false").lower() in ("true", "1", "yes")
BTC_ETH_CORR_WINDOW = 30
BTC_ETH_CORR_MIN = 0.6

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


# ─── Phase K — momentum candidate assets (NOT live until promoted) ────────
# Same separation pattern as BREAKOUT_CANDIDATE_ASSETS: never iterated
# by main.py — only by tools/validate_momentum_candidates.py.
#
# Coins already covered in ASSETS (BTC/ETH/SOL/ADA/DOGE/DOT/HBAR/SHIB/XRP)
# are excluded. WEEX-unsupported (MATIC/SHIB/PEPE format variants) also
# skipped — the existing SHIB rows above only work because WEEX accepts
# the bare SHIBUSDT for spot but not for some perps; treat new symbols
# conservatively and let the validator's get_klines call surface mismatches.
_MOMENTUM_TOP30_NEW_COINS = [
    ("LTC",    "LTCUSDT"),    ("TRX",    "TRXUSDT"),
    ("BNB",    "BNBUSDT"),    ("AVAX",   "AVAXUSDT"),
    ("LINK",   "LINKUSDT"),   ("NEAR",   "NEARUSDT"),
    ("UNI",    "UNIUSDT"),    ("FIL",    "FILUSDT"),
    ("ETC",    "ETCUSDT"),    ("APT",    "APTUSDT"),
    ("ARB",    "ARBUSDT"),    ("ATOM",   "ATOMUSDT"),
    ("SUI",    "SUIUSDT"),    ("AAVE",   "AAVEUSDT"),
    ("OP",     "OPUSDT"),     ("INJ",    "INJUSDT"),
    ("RENDER", "RENDERUSDT"), ("TON",    "TONUSDT"),
    ("ICP",    "ICPUSDT"),
]


def _momentum_default(symbol: str, interval: str, name: str,
                       use_btc_filter: bool = True,
                       use_regime_gate: bool = True) -> dict:
    """Baseline momentum config — mirrors the ETH_1D / BTC_4H pattern with
    use_adx_filter ON (helps reject low-vol regimes that produce noisy
    EMA crosses). BTC correlation filter ON for alts, OFF for BTC itself.

    Trade params (TP1/TP2/SL/stale) follow the alt-friendly numbers used
    by ADA_4H et al. These are starting points — once an asset passes
    validation, the operator can tune the per-asset BACKTEST_STATS row
    and any params that the TradingView strategy report suggests.
    """
    return {
        "symbol":           symbol,
        "interval":         interval,
        "ema_fast":         20, "ema_slow": 50,
        "close_above":      "ema_fast",
        "atr_period":       14, "atr_sma_period": 20,
        "rsi_period":       14, "rsi_sma_period": 14,
        "rsi_min":          50, "rsi_max": 70,
        "macd_fast":        12, "macd_slow": 26,
        "macd_signal":       9, "macd_mode": "strict",
        "use_pmo":          False,
        "use_volume_filter": False,
        "use_mfi_filter":    False,
        "use_adx_filter":    True,
        "adx_period":       14, "adx_threshold": 18,
        "use_btc_filter":    use_btc_filter,
        "btc_ema_period":   50,
        # L.2: regime gate (Phase K promotions default ON; legacy stays
        # untouched). Blocks LONG entries during strong_down + SHORT
        # entries during strong_up. The classifier needs >=200 bars so
        # short-window 1H configs still receive klines.
        "use_regime_gate":   use_regime_gate,
        "tp1_atr_mult":      1.8, "tp2_atr_mult": 4.0,
        "sl_atr_mult":       1.0,
        "tp1_close_pct":     0.50,
        "use_breakeven_after_tp1": True,
        "stale_bars":        12 if interval == "4h" else 5,
        "stale_threshold_mult": 0.5,
        "strategy_name":     name,
    }


def _expand_momentum_top30(tf: str) -> dict:
    suffix = "4H" if tf == "4h" else "1D"
    return {
        f"{name}_{suffix}": _momentum_default(
            symbol, tf, f"{name} {suffix} Momentum")
        for name, symbol in _MOMENTUM_TOP30_NEW_COINS
    }


# Promotion round 1 (Jun 7 2026) — 12 momentum candidates cleared the
# gates (PF >= 1.5, n >= 5, max DD <= 15%):
#   TRX_4H    PF=2.68    n=16  WR=62.5%  total= +6.4%  DD=2.1%
#   AVAX_4H   PF=5.99    n=6   WR=83.3%  total= +6.4%  DD=1.3%
#   LINK_4H   PF=2.45    n=9   WR=77.8%  total= +6.5%  DD=2.4%
#   FIL_4H    PF=1.64    n=7   WR=71.4%  total= +5.1%  DD=8.0%
#   APT_4H    PF=1.61    n=8   WR=50.0%  total= +6.1%  DD=6.9%
#   ARB_4H    PF=inf     n=9   WR=100%   total=+27.4%  DD=0.0%
#   SUI_4H    PF=3.23    n=5   WR=80.0%  total= +6.9%  DD=3.1%
#   AAVE_4H   PF=100.34  n=6   WR=83.3%  total=+17.1%  DD=0.2%
#   INJ_4H    PF=13.70   n=11  WR=72.7%  total=+35.5%  DD=2.7%
#   RENDER_4H PF=4.40    n=6   WR=66.7%  total=+12.7%  DD=3.0%
#   NEAR_1D   PF=inf     n=5   WR=100%   total=+41.2%  DD=0.0%
#   SUI_1D    PF=4.17    n=14  WR=85.7%  total=+66.4%  DD=11.2%
# PF=inf means no losses in the 1000-bar window — using 999.0 sentinel
# (same as metrics.py recovery_factor uses for divide-by-zero cases).
# Phase L.1b: each row carries its true backtest window in `years` so the
# projection's annualization divisor is correct. 4H rows: 1000 bars =
# 0.46yr; 1D rows: 1000 bars = 2.74yr. Adding `years` here is REQUIRED —
# the projection function falls back to the global BACKTEST_YEARS=5.3
# without it, which UNDER-annualizes 4H rows by ~11×.
_MOMENTUM_PROMOTIONS = [
    ("TRX_4H",    "TRXUSDT",    "4h",
     {"pf":   2.68, "trades": 16, "pnl_pct":  6.4, "dd_pct":  2.1, "years": 0.46}),
    ("AVAX_4H",   "AVAXUSDT",   "4h",
     {"pf":   5.99, "trades":  6, "pnl_pct":  6.4, "dd_pct":  1.3, "years": 0.46}),
    ("LINK_4H",   "LINKUSDT",   "4h",
     {"pf":   2.45, "trades":  9, "pnl_pct":  6.5, "dd_pct":  2.4, "years": 0.46}),
    ("FIL_4H",    "FILUSDT",    "4h",
     {"pf":   1.64, "trades":  7, "pnl_pct":  5.1, "dd_pct":  8.0, "years": 0.46}),
    ("APT_4H",    "APTUSDT",    "4h",
     {"pf":   1.61, "trades":  8, "pnl_pct":  6.1, "dd_pct":  6.9, "years": 0.46}),
    ("ARB_4H",    "ARBUSDT",    "4h",
     {"pf": 999.0,  "trades":  9, "pnl_pct": 27.4, "dd_pct":  0.0, "years": 0.46}),
    ("SUI_4H",    "SUIUSDT",    "4h",
     {"pf":   3.23, "trades":  5, "pnl_pct":  6.9, "dd_pct":  3.1, "years": 0.46}),
    ("AAVE_4H",   "AAVEUSDT",   "4h",
     {"pf": 100.34, "trades":  6, "pnl_pct": 17.1, "dd_pct":  0.2, "years": 0.46}),
    ("INJ_4H",    "INJUSDT",    "4h",
     {"pf":  13.70, "trades": 11, "pnl_pct": 35.5, "dd_pct":  2.7, "years": 0.46}),
    ("RENDER_4H", "RENDERUSDT", "4h",
     {"pf":   4.40, "trades":  6, "pnl_pct": 12.7, "dd_pct":  3.0, "years": 0.46}),
    ("NEAR_1D",   "NEARUSDT",   "1d",
     {"pf": 999.0,  "trades":  5, "pnl_pct": 41.2, "dd_pct":  0.0, "years": 2.74}),
    ("SUI_1D",    "SUIUSDT",    "1d",
     {"pf":   4.17, "trades": 14, "pnl_pct": 66.4, "dd_pct": 11.2, "years": 2.74}),
]
for _name, _symbol, _interval, _stats in _MOMENTUM_PROMOTIONS:
    _cfg = _momentum_default(_symbol, _interval,
                              f"{_name.replace('_', ' ')} Momentum")
    _cfg["backtest_stats"] = _stats
    ASSETS[_name] = _cfg
del _name, _symbol, _interval, _stats, _cfg

# Promoted keys filtered out of candidates so re-runs don't re-test them
_MOMENTUM_PROMOTED_KEYS = {name for name, _s, _i, _bts in _MOMENTUM_PROMOTIONS}

# DD-tight recovery variants for momentum 1D candidates that failed only
# on DD>15%. Same baseline params but sl_atr_mult tightened 1.0 → 0.7,
# tp1_atr_mult 1.8 → 1.5 (proportional). Reduces tail risk on adverse
# moves; trade-off is more frequent stop-outs on noise. Targets:
#   LINK_1D  PF=1.76  total=+15.5%  DD=18.7%  (failed DD by 3.7pp)
#   FIL_1D   PF=1.59  total=+10.5%  DD=17.7%  (failed DD by 2.7pp)
#   APT_1D   PF=3.65  total=+52.5%  DD=18.1%  (failed DD by 3.1pp, strongest PF)
#   LINK_1D  also a near-miss — adding 4H variant of same TS pattern
# Suffix _TS = "Tight Stop"
_MOMENTUM_DD_RECOVERY_TARGETS = [
    ("LINK_1D_TS", "LINKUSDT", "1d", "LINK 1D Momentum (TS)"),
    ("FIL_1D_TS",  "FILUSDT",  "1d", "FIL 1D Momentum (TS)"),
    ("APT_1D_TS",  "APTUSDT",  "1d", "APT 1D Momentum (TS)"),
]


def _momentum_tight_stop(symbol: str, interval: str, name: str) -> dict:
    """DD-recovery variant: tighter SL + scaled TPs to maintain R-multiple."""
    cfg = _momentum_default(symbol, interval, name)
    cfg["sl_atr_mult"]  = 0.7   # was 1.0
    cfg["tp1_atr_mult"] = 1.5   # was 1.8  (keep ~2.1 R/R ratio at TP1)
    cfg["tp2_atr_mult"] = 3.5   # was 4.0  (keep ~5R at TP2)
    return cfg


MOMENTUM_CANDIDATE_ASSETS = {
    **{k: v for k, v in {
            **_expand_momentum_top30("4h"),
            **_expand_momentum_top30("1d"),
        }.items() if k not in _MOMENTUM_PROMOTED_KEYS},
    # DD-tight recovery variants
    **{name: _momentum_tight_stop(sym, tf, title)
        for name, sym, tf, title in _MOMENTUM_DD_RECOVERY_TARGETS},
}
