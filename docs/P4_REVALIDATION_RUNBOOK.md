# P4 — Revalidation Runbook (Jul 2026 rebuild)

Gate-driven, not calendar-driven. Every step has an entry gate (what must
be true to start) and an exit gate (what must be true to advance). If a
gate fails, the strategy goes back one step — no exceptions, no tuning
mid-window.

**What changed and why revalidation is mandatory:** P1–P3 rebuilt the
execution layer (exchange-native brackets, OCO hygiene), the backtest
harness (conservative intra-bar fills + 0.15% round-trip costs), and
every strategy's exit logic (ATR/invalidation exits replacing tight %
stops). Old backtest numbers and the first 14-day paper window described
a different system. Nothing carries over.

---

## Step 0 — Deploy the P1–P3 code (operator, ~5 min)

```bash
ssh bot@143.198.20.44
cd /home/bot/crypto-bot && git pull
venv/bin/python -m pytest tests/ -q          # expect all green
sudo systemctl restart crypto-momentum crypto-whale crypto-funding \
    crypto-breakout crypto-pair crypto-scalp crypto-crossover
journalctl -u crypto-scalp -n 20 --no-pager   # confirm cycle 1 + signal lines
```

Entry gate: none. Exit gate: all units active, heartbeats fresh on the
dashboard, no tracebacks in the first 3 cycles of each unit.

Keep `DRY_RUN=True` and all pause flags as they are. Bots that were
paused stay paused — revalidation decides what un-pauses.

## Step 1 — Honest local replay (same day)

Re-run the fixed replay harness (intra-bar exits + cost model) for every
strategy that will trade:

```bash
venv/bin/python tools/backtest_replay.py --bot scalp     --bars 5000
venv/bin/python tools/backtest_replay.py --bot crossover --bars 1195
venv/bin/python tools/backtest_replay.py --bot breakout  --bars 1000
```

Exit gate per asset: **PF ≥ 1.3 after costs AND avg-win % > 0.5%**
(edge ≥ ~3× round-trip cost). Assets that fail stay out of the live
universe — mark them candidates, do not tune them this pass.

Known limitation: these windows are 17–50 days. That is why Step 2
exists.

## Step 2 — TradingView long-window confirmation (operator + MCP, ~1 evening)

Scripts: `tools/pine/crossover_n3.pine`, `tools/pine/scalp_m3.pine`
(already saved to the TV account; see `tools/pine/README.md`).

Per asset, 2022-01-01 → today, bar magnifier ON, costs left as-is:

Exit gate: **PF ≥ 1.3, n ≥ 50 (scalp) / n ≥ 30 (crossover), max DD ≤ 15%,
avg win > 0.5%** — AND directional agreement with Step 1 on the
overlapping window (same expectancy sign, PF within ±0.4). Disagreement
= distrust both, investigate the harness before proceeding.

Only assets passing BOTH Step 1 and Step 2 advance.

## Step 3 — 48h paper shakedown (mechanical, not statistical)

Un-pause only the Step-2 survivors. This step verifies MECHANICS, not
edge — 48h proves nothing statistically:

- [ ] Entries carry exchange-native brackets (check WEEX order log /
      DRY_RUN order dump: `slTriggerPrice` on MARK_PRICE, TP on
      CONTRACT_PRICE)
- [ ] No orphaned pending orders after any close (P1.2 cancel-before-entry)
- [ ] Exit reasons in the journal match the new logic (Invalidation
      Exit / Emergency SL / Time Limit / ATR TP-SL — no legacy "SL Hit
      -1.5%" style rows)
- [ ] Slippage log: |paper fill − signal close| median ≤ 0.05%
- [ ] Kill-switch live-fire: inject a synthetic −$160 day into a COPY of
      the journal, confirm `should_pause` trips at the −$150 (3%)
      threshold, then restore
- [ ] Discord notification on every open AND close

Exit gate: all boxes checked. Any failure = fix, restart the 48h clock.

## Step 4 — 14-day paper window (statistical)

Same rules as previous windows, now on honest machinery:

Exit gate per bot: **live-paper PF ≥ 1.3, ≥ 10 closed trades, kill
switch never tripped, slippage still ≤ 0.05% median.**

Weekly during the window, check exit-reason distribution:
- SL-share of exits > 60% → brackets still too tight; back to Step 1
- Time-Limit share > 40% (scalp) → entries firing into drift; back to Step 1

Bots at PF 1.0–1.3 → hold 14 more days. Bots < 1.0 → back to Step 1
with the live trade log as the diagnostic input.

## Step 5 — Micro-live ($50 total, one bot)

Entry gate: Step 4 passed + operator explicitly flips `DRY_RUN=False`
(this is never automated) + trade-scope-only API keys confirmed (never
withdraw scope).

- One bot only — the Step-4 winner with highest PF and lowest DD.
- Sizing floor: $10 margin × 10x = $100 notional per trade, ≤ $50 total
  margin at risk.
- Hard stops: −$50/day or −$150 cumulative → revert to paper
  automatically (kill switch) and post-mortem before any retry.

Exit gate: **10 closed live trades with live PF ≥ 1.0 AND per-trade
fill-vs-paper deviation ≤ 0.1%** (this verifies the fee/slippage model
against real WEEX statements — the last untested assumption).

## Step 6 — Scale ladder

After 2 clean micro-live weeks: add ONE bot **or** double sizing — never
both in the same step. Repeat the 2-week observation between rungs.
Any hard-stop trip resets the ladder one rung.

---

## Standing rules (all steps)

- `DRY_RUN=True` until Step 5's explicit operator flip.
- No parameter tuning inside an observation window — tuning restarts it.
- Every gate decision gets a dated note in the plan file (audit trail).
- The dashboard's per-bot cards + kill-switch panel are the daily check;
  the journal queries in Phase O §Task 3 are the weekly deep check.
- Correlation gate (`USE_BTC_ETH_CORR_GATE`) and the momentum MACD/EMA200
  gates stay OFF until a Step-1-style A/B replay shows they help — they
  are validated the same way as everything else, not assumed.
