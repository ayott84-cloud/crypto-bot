# Ticket: activate breakout's silent no-op regime gate (A/B first)

**Found during P4 Step-1 parity verification (Jul 4 2026).**

## The defect

`breakout_main._compute_indicators` produces atr/atr_sma/adx + Donchian
— **no EMA columns**. `regime.classify_from_df` reads `ema_fast`/
`ema_slow` (or `ema20`/`ema50`), gets None, and classifies trend
"unknown"; `gate_blocks_direction("unknown", …)` never blocks. Result:
the L.2 regime gate that `use_regime_gate: True` is supposed to arm on
every Phase-K breakout asset **has never blocked a single live entry**.
(The L.2 peer review predicted exactly this failure mode and the
`classify_from_df` wrapper fixed it for momentum's columns — breakout's
were missed.)

## Why it matters now

The 2-year honest replays put the surviving breakout assets at PF
1.23–1.43 with 28–64% drawdowns — classic unfiltered trend-following.
A functioning trend-regime gate (block LONG in strong_down, SHORT in
strong_up) is the highest-probability lever to push PF toward the 1.5
promotion gate and, more importantly, cut the drawdowns.

## The rule

Per the P4 runbook standing rules: **unvalidated gates stay OFF until an
A/B replay shows they help.** Activating this gate changes live behavior
— it must be replay-proven first.

## Plan

1. Add `ema_fast` (20) / `ema_slow` (50) columns in
   `breakout_main._compute_indicators` — this alone activates the live
   gate for every cfg with `use_regime_gate: True`, so DO NOT ship it
   until step 3 passes.
2. Model the same gate in `replay_breakout` (call `classify_from_df` +
   `gate_blocks_direction` after `analyze_breakout_entry`, mirroring
   run_cycle) — keeps replay/live parity through the change.
3. A/B over the same 17,000-bar windows on the five kept assets
   (SOL_4H, ETH_4H, DOGE_1H, ETH_1H, INJ_1H):
   gate-off (current baseline numbers) vs gate-on.
   Ship gate-on only where PF improves ≥ 0.10 AND max DD does not
   worsen, per the Phase M/N sweep pass criteria.
4. Same A/B discipline applies to the funding veto when a historical
   funding-rate source is found, and to per-asset trailing-exit tuning
   (Step-1 showed trailing helped BTC but gutted INJ/DOGE/AAVE).

## Status

- [x] Steps 1-2 implemented replay-side (Jul 4): `replay_breakout(...,
      regime_gate_active=True)` / CLI `--regime-gate`. EMA columns are
      computed in the REPLAY only — live stays gate-inert until the A/B
      verdict. Wiring test: tests/test_breakout_replay_parity.py::
      test_regime_gate_arm_blocks_misaligned_entries.
- [ ] A/B run + per-asset verdicts recorded here. Droplet commands:
      `venv/bin/python tools/backtest_replay.py --bot breakout --bars 17000 --source binance`
      (baseline — matches the Jul 4 Step-2 numbers) then the same with
      `--regime-gate`. Ship per asset only where PF +0.10 and DD not worse.
- [ ] Live flip (only for winners): add ema_fast/ema_slow to
      breakout_main._compute_indicators + droplet deploy
