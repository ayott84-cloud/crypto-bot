# Dashboard UI Enhancement & Redesign Options (Jul 2026)

Audit performed with the dashboard-design / interface-design skill lenses
against the live build (974 KB static HTML, 9-tab sidebar, Quant Cockpit
token system). Browser QA ran during P5c: page loads with zero console
errors after the TWLC v5 chart fix (`c4e2757`).

## What's already solid — keep, don't churn

- **Token system** (`static/css/tokens.css`): true single source of truth,
  8px grid, restrained radii, per-bot identity colors, tabular-figure
  mono for numbers. This is better than the skill's reference patterns —
  the redesign should build ON it, not replace it.
- **Layout**: sidebar + main matches the canonical dashboard pattern;
  colophon strip gives operator/env/freshness at a glance.
- **Why-silent panels**: the single highest-value operator feature; no
  mainstream dashboard pattern does this. Protect it in any redesign.
- **Honesty affordances**: confidence pills on small-n projection rows,
  PF display caps — keep expanding this direction.

## Tier 1 — operational gaps (high value, ship first)

### 1.1 Kill-switch status panel  *(gap found in P5c QA — the M/N plan
specified it and it was never built)*
Overview panel reading `kill_switch.status_summary()`: one row per owner
(8 bots), status dot (green armed / red TRIPPED + reason + cooloff
remaining), plus the effective daily-DD threshold (now the tighter of
-$500 / -3% = **-$150** after P3.6). Without this, a tripped breaker is
invisible until the operator greps journalctl.
*Effort: S (2-3h). Files: dashboard.py (context builder), new
`components/kill_switch_panel.html.j2`, overview tab.*

### 1.2 Live bracket display on open positions
P5a now persists `sl_price` / `tp_price` / `bracket_kind` / `exit_kind`
on every position at entry. Surface them in `bot_positions_panel`: SL /
TP columns + **distance-to-trigger** as a mini progress bar (entry →
current → trigger). Directly answers "how close is this trade to
stopping out" — today the operator computes it mentally.
*Effort: S-M (3-4h). Files: bot_positions_panel.html.j2, dashboard.py
`_v2_open_positions_for_bot`.*

### 1.3 Exit-reason distribution panel (per bot, trailing 14d)
The P4 runbook's weekly check ("SL-share > 60% = brackets too tight;
Time-Limit > 40% = entries firing into drift") is a manual journal
query today. Render it as a horizontal stacked bar per bot with the two
threshold markers drawn in. Makes the runbook check a 2-second glance.
*Effort: M (4h). Files: dashboard.py (journal aggregation), new
component, per-bot tabs + overview.*

### 1.4 Revalidation gate tracker
A small stepper on Overview showing each bot's position in the P4
pipeline (Step 0 deploy → 1 replay → 2 TV → 3 shakedown → 4 paper-14d →
5 micro-live → 6 scale), driven by a tiny `revalidation_status.json`
the operator (or the bots) update. Replaces "which step were we on?"
with a glance.
*Effort: M (4-5h).*

## Tier 2 — UX polish on existing surfaces

### 2.1 Theme-toggle chart re-init
Charts capture theme colors once at init; toggling light/dark leaves
already-rendered charts in the old palette. Fix: on toggle, destroy +
re-init visible charts (they're lazy-loaded already, so the hook
exists in `initAssetDropdowns`).
*Effort: S (1-2h). File: static/js/dashboard.js.*

### 2.2 Sticky table headers + sortable per-bot trade panels
The main Trade Log has sort/search; the per-bot closed-trades panels
don't. Reuse the same JS (it's generic on `data-sort` attributes).
*Effort: S (2h).*

### 2.3 Empty states
Bots with zero trades render bare tables. Add the skill-recommended
empty state: monogram + one line ("No closed trades yet — paper window
opened Jul 4") + the bot's current gate step (ties into 1.4).
*Effort: S (2h).*

### 2.4 Number-change flip animation on stat cards
The Phase D spec called for a 240ms Solari-flip on value changes;
never implemented. With 5-min static rebuilds it only fires on reload —
low value unless/until auto-refresh (3.2) ships. Defer accordingly.
*Effort: S, but defer behind 3.2.*

### 2.5 Mobile pass
Sidebar collapses but tables overflow on <768px. Apply the skill's
card-list pattern to the Trade Log + positions panels only (the operator
checks phones for status, not analysis).
*Effort: M (4-6h).*

## Tier 3 — structural options (pick at most one)

### 3.A "Mission Control" overview redesign  *(recommended)*
Reorganize Overview around the operator's three questions, in order:
**Am I safe?** (kill-switch panel + open-risk total + daily PnL vs
-$150 breaker) → **What changed?** (last 24h: trades closed, gates
tripped, watchdog events) → **Why is X quiet?** (existing why-silent
board). Bot cards shrink to a dense 8-row status table (name, state,
PF-14d, net, open positions, last trade age) — the current card grid
spends ~40% of the viewport repeating labels.
*Effort: L (1-2 days). Uses only existing tokens/components.*

### 3.B Live-ops split ("Trading" vs "Research" views)
Two sidebar sections: Trading (Overview, Trade Log, per-bot tabs) and
Research (Projection, backtest stats, gate tracker, exit-reason
analytics). Mirrors the operator's two modes — 5-second status checks
vs deep weekly reviews.
*Effort: M (1 day, mostly template reshuffling).*

### 3.C Full visual re-skin
Not recommended: the Quant Cockpit direction is distinctive and
consistent; a re-skin buys nothing operational and risks the 40+
templates/components that already agree with each other.

## Suggested sequence

1. Tier 1.1 + 1.2 (one PR: safety visibility + bracket display)
2. Tier 1.3 + 1.4 (one PR: runbook-as-UI)
3. Tier 3.A overview redesign (after a week of living with Tier 1)
4. Tier 2 items opportunistically alongside

Total for tiers 1 + 3.A: roughly 3-4 focused days, zero new
dependencies, all within the existing template/token architecture.
