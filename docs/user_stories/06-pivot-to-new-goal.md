# 06 — Pivot to a new goal mid-plan

## Persona

**Marcus**, 45, masters athlete. Was building toward a fall marathon
(Berlin 2026, 2:55 BQ attempt). Six weeks into a 16-week build, his
employer offers a relocation he can't refuse — Berlin is off the table.
He pivots to **NYC Marathon 2026-11-01** instead, six weeks later than
Berlin.

The pivot also brings a new constraint: NYC is a hilly course with
notable bridges; Berlin was flat. The goal time also relaxes — he wants
to "race well, not chase a number."

## Goal

Take an in-flight plan, re-anchor it on a different race, with a different
date, profile, and goal type. Carry forward the work already done, don't
restart from scratch, and capture the reasoning so 2 months later he
remembers why the chain looks how it does.

## Step-by-step

### 1. Update the race calendar

```yaml
# athlete/race-calendar.yaml
- id: berlin-2026
  date: 2026-09-27
  type: marathon
  priority: A
  status: cancelled
  cancelled_reason: "Relocation conflict 2026-08."
- id: nyc-2026
  date: 2026-11-01
  type: marathon
  priority: A
  location: New York, NY
  course_notes: "Rolling, 5 bridges, notable Verrazano start + late Central Park climb."
  target: "race well, no specific time"
```

> **GAP — race statuses aren't a typed concept.** `status: cancelled` is
> ad-hoc YAML; the composer doesn't formally know "skip this".

### 2. Tell the agent about the pivot

```
"Berlin is cancelled, NYC marathon 2026-11-01 is now the A-race.
Same overall fitness goal but I'm not chasing 2:55 anymore — race
well on a hilly course. Re-anchor the plan."
```

The agent should:

1. Detect Berlin's `status: cancelled`.
2. Pick NYC as the new A-race target.
3. Recompute runway from today (W7 of original plan, ~26 weeks from NYC).
4. Decide whether to **extend** the existing plan or **rebuild**:
   - Original chain: 16 weeks (Berlin) — 6 done, 10 remaining.
   - New chain: 26 weeks (NYC) — 26 ahead.
   - The composer should *extend* by inserting an aerobic-base block
     between the current point and the original build phase.
5. Re-validate against composition rules — passes.
6. Add a hill-emphasis note to build phase (NYC course profile).

Today this is a careful prompt. **[GAP — US-8 + composer extension].**
The composer can take a seed chain and adjust runway, but `coach plan
amend --switch-target nyc-2026` doesn't exist.

### 3. Verify the pivot

```
/coach-dashboard-macro
```

Sees:
- Original Berlin chain: dotted/greyed-out.
- New NYC chain: solid.
- Carry-forward: 6 weeks of completed base + early build are kept.
- Insertion: 10-week extension at base/build threshold to absorb the
  longer runway.
- Calibration debt: "course profile differs (hilly vs flat); session
  emphases recommended" — actionable item.

> **GAP — visualising the carry-forward + insertion.** A pivot dashboard
> that shows "what's preserved, what's new, what's discarded" would help
> Marcus trust the change. Today the macro view shows the *new* chain
> only.

### 4. Goal change without race date change

A different scenario: Marcus's race date is preserved but his **goal
shifts** — instead of BQ-chase, he wants to "use this build to set up
an ultra debut in March 2027." Same Berlin, but Berlin is now a
*preparatory* event toward a 50K trail in March.

This is harder. The same race is now a B-race, not an A-race. Build
emphasis shifts toward durability/time-on-feet, away from goal-pace
threshold work.

The agent edits:
- `athlete/race-calendar.yaml` — adds 50K, demotes Berlin to B.
- `athlete/goals.yaml` — adds an "ultra durability" goal.
- `plans/<id>/goal.yaml` — updates A-race reference.
- `plans/<id>/plan.yaml` — re-emphasises base/durability, dampens
  speed work.
- `changelog.md` — full reasoning entry.

> **GAP — goal-driven phase emphasis.** Phases are templates with fixed
> session distributions. A "durability emphasis" should *modify* the
> existing phase, not require a new template. The composer doesn't
> currently support phase modifiers.

### 5. The "two A-races" pivot

A third common case: **add** a new A-race without removing the original.
Marcus signs up for both Berlin (Sep) and NYC (Nov) — 5 weeks apart.

This requires a multi-A chain, which the composer doesn't produce
(see story 02). The user has to pick one as the "real" A-race and treat
the other as a B-priority overreach.

> **GAP — multi-A composition.** Two A-races within 6 weeks of each
> other is common (back-to-back majors, Kona+regionals, NYC+CIM). The
> composer should produce a peak→short-recovery→peak chain.

### 6. Decision audit trail

After all the changes, Marcus runs:

```
/coach-dashboard-decisions --plan 2026-marcus-marathon
```

Sees timeline:
```
2026-04-12  Plan created (Berlin 2026, BQ attempt 2:55)
2026-05-31  Pivot to NYC 2026 — relocation forced Berlin cancel.
            Goal relaxed to "race well, no time target."
            Carry-forward: 6 weeks completed; 10 weeks inserted.
2026-06-04  Hill-emphasis added to build phase (NYC course profile).
…
```

Each entry has a "why" and a link to the wellness/load context at the time.

## What works today

- Bootstrap-plan can be re-run to amend an existing plan; it appends a
  changelog entry on restructure.
- Decisions dashboard shows the audit trail.
- Composer handles runway extension.

## Gaps surfaced

1. **Race lifecycle as a typed concept.** `status: cancelled | confirmed
   | tentative` should affect composer behaviour, not be ad-hoc YAML.
2. **`coach plan amend --switch-target`.** Atomic re-anchoring with
   carry-forward is the most-requested editing operation; today it's a
   careful prompt + manual diff review.
3. **Pivot visualisation.** "What's preserved, what's inserted, what's
   discarded" should be visible — not just the new chain.
4. **Goal-driven phase emphasis modifiers.** Same template, different
   session distribution based on the goal (durability emphasis, speed
   emphasis, low-volume emphasis).
5. **Multi-A-race composition.** Two A-races within 4-8 weeks is common;
   composer needs a peak→recover→peak shape.
6. **Goal succession.** "This build feeds the next goal" should carry
   structural choices forward — base depth, durability, etc. — into
   the next plan automatically.
