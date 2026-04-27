# 04 — Weekly snapshot, the Sunday rhythm

## Persona

**Any active Tempo user** mid-block. Has an active plan in `plans/<id>/`,
4+ weeks of synced data, a routine of `coach sync` running daily.

## Goal

The weekly cadence — Sunday evening: review last week, draft the next,
push it to the calendar — should be 10 minutes of decision-making, not
30 minutes of context reconstruction.

## Step-by-step

### 1. Sunday 7pm — open the laptop

```
coach sync
coach status
```

`coach status` (illustrative):
```
Active plan: 2027-half-ironman, week 12 of 53 (return_to_3sport)
This week: 4/5 sessions completed, 6h 22m total
Last sync: 12 minutes ago
Calibration debt: 1 item (FTP probe due — last set 2026-01)
```

> **GAP — `coach status` is currently just `coach doctor`'s active-plan
> check.** The richer single-line summary above is aspirational.

### 2. Review the week

```
/review-week
```

Skill output is a markdown post-mortem:

```
# Week 2026-W17 review

## Adherence
- Planned 5 sessions / 7h 00m / 410 TSS
- Actual  4 sessions / 6h 22m / 366 TSS  (89%)
- Skipped: Thu threshold bike (work travel)

## Wellness trend
- HRV mean 62 (7d), down 4% vs prior week
- Sleep mean 7h 12m (target 7h 30m)
- Soreness flag: right calf 2/10 Tue→Wed, resolved

## Load trajectory
- CTL 58 → 60 (+2)
- TSB -8 → -6
- ATL 66 → 66
- On-track for phase target (CTL 65 by W14)

## Lessons
- Thu travel disruption — 2nd time this block. Pattern: Thu sessions
  are at risk. Recommendation: shift weekly key bike to Wed.
- Calf flag resolved — no carryover.

## Decisions logged
- 2026-04-29: long run capped at 14km Z1 (calf precaution)
- 2026-05-02: skipped Thu bike (travel)
```

This is rendered, written to `plans/<id>/weeks/2026-W17.md` under a
`## Review` heading, and `log_decision` rows are created for each.

### 3. Single-week dashboard

```
/coach-dashboard-week 2026-W17
```

Renders an HTML artifact: planned vs actual chart, wellness sparklines,
TSS pie by sport, changelog excerpts. Sean glances, confirms, doesn't
need to dive deeper.

### 4. Draft next week

```
/plan-training-week 2026-W18
```

Preflight assembles the structured brief:
- 14d adherence: 86%
- Wellness trend: neutral, HRV recovered
- Load: CTL 60 → target 65 by W14, on-track
- Phase template (return_to_3sport, week 4 of 4): walk/jog progression
- Active flags: none
- Last 4 weeks of the same weekday (pattern recognition): Thu travel risk

The agent drafts the week. Pattern-aware suggestion: "Thu has been
disrupted twice — proposing key bike on Wed, easy swim Thu (low cost
to skip)."

> **[GAP — pattern recognition].** Today the agent doesn't actually
> compute "Thu has been disrupted twice in the last 4 weeks." A pattern
> miner over `coach.db.sessions` (planned vs actual by weekday) would
> generate these recommendations as preflight signals.

### 5. Diff and edit

```
git diff plans/2027-half-ironman/weeks/
```

Sean reads, tweaks the long ride start time (5am, weather), saves.

### 6. Push

```
coach push-week 2026-W18 --dry-run
```

Output:
```
Plan: 2027-half-ironman
Week: 2026-W18 (2026-05-04 to 2026-05-10)
Events to upsert: 6
  + Mon 06:00  Z2 swim 2500m
  + Tue 18:00  Trainer 90' sweet spot
  + Wed 18:00  Threshold bike 60' (key)
  + Thu 06:30  Easy swim 2000m
  + Sat 05:00  Long bike 3:00 + 15' run
  + Sun 09:00  Long run 75'
Re-running this command will not create duplicates (idempotent on
plan_id=2027-half-ironman, week=2026-W18).
```

```
coach push-week 2026-W18
```

Six tagged events appear in intervals.icu, ready for his Garmin.

### 7. Decision dashboard — once a month

```
/coach-dashboard-decisions --scope plan
```

Timeline of every adjustment in the plan, with wellness + adherence
evidence. Sean uses this for monthly self-review and conversations with
his PT/coach.

## What works today

- `/review-week` and `/plan-training-week` exist and run.
- `coach push-week` is wired to `bulk_upsert_tagged_events` (Phase 1 GAP
  closed).
- All three dashboards render (week, macro, decisions).
- Decisions are logged to `coach.db.decisions`.

## Gaps surfaced

1. **`coach status` should be richer**, not just a doctor subset. It
   should show: active plan + phase, current week's adherence, calibration
   debt count, time since last sync, current CTL/ATL/TSB.
2. **Pattern miner over historical adherence.** "Thu sessions miss 40%
   of the time when you're traveling that week" should be a preflight
   signal, not a thing Sean has to spot manually.
3. **Push-week feedback loop.** After push, no automatic verification
   that the events appeared correctly. A `coach push-week --verify`
   that re-fetches and diffs would close the trust gap.
4. **Dashboard-driven amendments.** When the macro dashboard shows
   "CTL is 5 points below plan target" the user should be able to click
   into a "rebalance phase" action. Today dashboards are read-only.
5. **Comparison views.** "Show me this week vs the same week of last
   year's plan" or "show me my best-adherence weeks of this build" —
   no built-in way to slice.
6. **Calendar-conflict detection on push.** If intervals.icu already has
   a manually-created event in the same slot, push should warn rather
   than overwriting silently.
