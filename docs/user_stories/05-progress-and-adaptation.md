# 05 — Mid-cycle progress check + adaptation

## Persona

**Priya**, 29, training for her second IM 70.3. Week 8 of 16 in her build
phase. Has been syncing diligently. Her A-race is in 8 weeks and she's
asking the meta-question:

> "Am I on track? And if I'm not, what should change?"

## Goal

Take stock at the macrocycle midpoint. Compare reality to plan. Decide
whether to stay the course, accelerate, or back off. Get this in 15
minutes, not as a 2-hour spreadsheet exercise.

## Step-by-step

### 1. The "how am I doing?" question

She asks the agent in plain language:

> "Pull together a midpoint review for me — I'm 8 weeks into a 16-week
> half-IM build and I want to know if I'm on track to my 4:55 goal."

The agent should produce something like:

```
# Midpoint review — 2026-W22

## Plan target vs actual
                 Plan      Actual    Delta
CTL              82        76        −6      ⚠ trending below
TSB              -10       -3        +7      ✓ less fatigued than expected
Run threshold    4:42/km   4:48/km   slower  ⚠ probe in W21 came in slow
Bike FTP         245W      252W      +7W     ✓ ahead
Swim CSS         1:48/100  1:46/100  −2s     ✓ ahead

## Adherence
- Sessions: 89% completed
- TSS:      82% of plan (lower because of the under-achieved running)
- Wellness: green throughout, no injury flags

## Hypothesis
You are bike/swim ahead and run behind. Two contributing factors:
1. Run volume hasn't grown as planned — you've capped at 28-32 km/wk
   when plan called for 35-40.
2. Tempo runs are 5-10s/km off prescribed pace — likely because the
   threshold pace was set from a half-marathon test 5 months ago that
   may no longer be representative.

## Options
A. Stay the course — accept run will be conservative, lean on bike.
B. Re-test run threshold this week, recalibrate zones, push run volume
   to plan target over W23-W25 (+10%/wk cap).
C. Reduce A-race goal to 5:05, derisk run.

Recommendation: B. The bike/swim gains are real money; the run is a
calibration error not a fitness error.
```

### 2. Where this comes from today

Today the agent assembles this *in conversation*, by:
- Reading `plans/<id>/plan.yaml` for targets.
- Calling `coach-db.get_load_curve` for actual CTL/ATL/TSB.
- Calling `coach-db.get_adherence` for completion ratios.
- Running `coach-db.compare_plan_to_actual` for delta computation.
- Reading the changelog for context.
- Asking Priya for her recent test results.

> **GAP — there's no `/midpoint-review` skill.** The above is what a
> good prompt produces *if the agent remembers all the right calls*.
> A typed Skill that always assembles the same brief would be a major
> usability win.

### 3. Auto-test detection

The agent's "threshold pace probe was 5 months ago" insight requires it
to know **when each zone was last set**. Today `athlete/profile.yaml`
stores zones but not their provenance.

> **GAP — zones with provenance.** Each zone should be a struct:
> `{ value, set_at, source: "race_result" | "field_test" | "manual_estimate" }`
> Calibration debt would then auto-flag stale zones. Today this is in
> the agent's head.

### 4. Adopting option B — recalibration plan

Priya picks B. The agent:

1. Inserts a 5K time trial into Wed of W22.
2. Adds a "recalibrate zones from W22 5K" decision to changelog.
3. Re-drafts W23-W25 with +10%/wk run volume ramp respecting R-13.

But — none of those are atomic CLI ops. Each is "agent edits these files,
user reviews diff." Doable, but careful.

> **[GAP — US-8 again]**: `coach plan amend --insert-test 2026-W22-Wed
> --type 5K-TT --recalibrate-on-result` would express this as a
> first-class action.

### 5. Continuous progress signal

After W22's 5K test (run threshold updates from 4:42→4:38/km — actually
*ahead*), the agent re-runs the midpoint analysis automatically:

> "Update — your 5K test came in at 19:32. New threshold pace 4:38/km.
> The earlier 'run is behind' read was a calibration error. You are
> ahead on all three sports. Recommendation: hold the planned volume
> ramp, no goal revision."

> **GAP — proactive vs reactive.** Today this re-analysis only happens if
> Priya asks. A scheduled "every 4 weeks, run midpoint review" via the
> `loop` skill or a recurring journal entry would close that.

### 6. Adapting to over/under-recovery

Two weeks later (W24), wellness drops: HRV down 12%, sleep degraded,
soreness 4/10. Adherence drops to 60% as Priya unconsciously skips
sessions.

`/morning-check-in` flags:
> "HRV trend is in the bottom decile of your 90-day distribution.
> Recovery week recommended ahead of plan."

The plan called for W25 to be a recovery week. Agent proposes:
- Pull W25 recovery up by 1 week (i.e., W24 becomes recovery, W25
  becomes the build week).
- Shift the rest of the chain by 1 week — but the race is fixed, so
  taper is shortened by 1 week.
- Flag: SOFT R-15 (build:recovery balance) is satisfied; SOFT taper
  duration shortened from 3wk to 2wk — agent flags this for Priya's
  call.

She approves. Agent edits `plan.yaml`, recomputes phase boundaries,
appends changelog, calls `log_decision`.

> **GAP — taper duration is currently a phase template constant**, not
> an athlete-attribute. Some athletes need 3-week tapers, some can
> get away with 10 days. Without per-athlete taper-response data, the
> system can't make this trade-off well.

## What works today

- The data substrates needed (load curve, adherence, plan targets) are
  all queryable via `coach-db`.
- Decisions are logged for any plan adjustment.
- `/coach-dashboard-macro` shows the gantt + current position.

## Gaps surfaced

1. **`/midpoint-review` skill.** Codify the structured brief above so
   any user gets the same analytical depth from any conversation.
2. **Zones with provenance.** Each FTP/threshold/CSS value should carry
   `set_at` + `source` so calibration debt can flag staleness.
3. **`coach plan amend --insert-test` and `--recalibrate-on-result`.**
   Test insertion + zone re-derivation should be one CLI call.
4. **Scheduled meta-reviews.** Run midpoint analysis automatically every
   4 weeks; flag deviations in the morning check-in.
5. **Per-athlete taper modeling.** Today taper length is a phase-template
   constant. Should be calibrated from observed peak-week → race
   performance over time.
6. **Pattern-based recovery prediction.** When HRV / sleep / soreness all
   trend negative simultaneously, propose a recovery shift *before*
   adherence collapses, not after.
