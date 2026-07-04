# Pine v6 validation scripts (P2.4)

Long-window TradingView backtests for the two redesigned strategies. Our
local replay windows are 17–50 days (n=2–10 trades per asset) — far too
small to trust. TradingView has years of 1h/15m history; these scripts
port the bot logic faithfully so the long window can confirm or falsify
the redesigns **before** any live-lift decision.

| Script | Strategy | Chart | Assets |
|---|---|---|---|
| `crossover_n3.pine` | Crossover N.3 — 1h SMA20/50 cross, SMA200-slope + ADX(14)>20 + daily 9-MA regime gates, invalidation exit + 3.5×ATR emergency SL | 1h | ETHUSDT.P, SOLUSDT.P, XRPUSDT.P, LINKUSDT.P |
| `scalp_m3.pine` | Scalp M.3 — 15m vol-expansion breakout, volume + 1h-trend + RSI-extreme + daily-regime filters, 2.5×ATR SL / 1.5R TP / 16-bar time limit | 15m | BTCUSDT.P, ETHUSDT.P, XRPUSDT.P, DOGEUSDT.P, LINKUSDT.P |

## How to run (operator or TV MCP)

1. Open the asset's **perp** chart (e.g. `BINANCE:ETHUSDT.P`) at the
   script's timeframe.
2. Pine Editor → paste the script → Add to chart.
3. Strategy Properties → tick **"Use bar magnifier"** (Premium feature —
   models intra-bar SL/TP fills from lower-TF data, matching the bot's
   conservative fill model). If unavailable, note it: results will be
   slightly optimistic on bars that touch both SL and TP.
4. Set the date range to **2022-01-01 → today** (captures bear, chop, and
   bull regimes).
5. Record from the Strategy Tester: **PF, win rate, total trades, max
   drawdown %, avg trade %**.

Via the TradingView MCP instead: `pine_set_source` → `pine_smart_compile`
→ `chart_set_symbol`/`chart_set_timeframe` → `data_get_strategy_results`.

## Cost model already baked in

- Commission 0.075%/side ≈ **0.15% round trip** — same as
  `DEFAULT_ROUND_TRIP_COST_PCT` in `tools/backtest_replay.py`.
- 2 ticks slippage per fill.

Do **not** zero these out; the no-cost numbers are the ones that fooled
us in M.2/N.2.

## Acceptance gates (from the P4 runbook)

Per asset, over the full window:

- **PF ≥ 1.3** after costs
- **n ≥ 50** trades (crossover may land lower on 1h — require n ≥ 30 there)
- **max DD ≤ 15%**
- **avg win % > 0.5%** (edge must clear ~3× the round-trip cost)

Also compare the TV results to `tools/backtest_replay.py` on the
overlapping recent window — the two should agree directionally (same
sign of expectancy, PF within ~±0.4). If they disagree, distrust both
and investigate before proceeding.

## Fidelity notes

- Entries fill on next-bar open after a signal on a completed bar — same
  as the bots, which act on freshly closed bars.
- Brackets anchor to the actual fill price (`strategy.position_avg_price`),
  with ATR captured at signal time — same as `atr_bracket_prices` /
  `bracket_trigger_price` registering ATR at entry.
- Daily-regime and higher-TF series use `[1]` + `lookahead_off` (no
  repaint), mirroring the bot's "last completed daily bar" reads.
- The crossover script has **no TP** by design — the invalidation exit
  (close back through SMA20) is the profit mechanism; the emergency stop
  only guards gaps. This mirrors `check_crossover_exit_v3`.
- One position at a time per chart (the bots run one position per asset).
