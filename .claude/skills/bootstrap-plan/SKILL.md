---
name: bootstrap-plan
description: Turn a declared goal (race or non-race) into plan.yaml + rationale.md + goal.yaml. Re-runnable — amends existing plans with a changelog entry on restructure.
trigger: /bootstrap-plan <goal-id>
---

# Skill: bootstrap-plan

You are extending a coaching program for Sean. A goal has been declared in
`athlete/race-calendar.yaml` (race) or `athlete/goals.yaml` (non-race goal).
Produce the structural plan that every subsequent weekly planning session
will fill in.

**This skill drafts structure, not sessions.** Weekly sessions are the job of
`plan-training-week`, not here. Stop at phases + weekly TSS targets + key
session archetypes + race markers.

## Step 1 — Run preflight

```bash
uv run python .claude/skills/bootstrap-plan/preflight.py --goal-id <goal-id>
```

Read the JSON brief from stdout. If the script exits with an error (unknown
goal id, missing files), stop and surface the error to the user — don't
guess.

The brief contains: `goal`, `weeks_until_target`, `applicable_phase_template`,
`athlete_state`, `recent_load`, `active_injuries`, `hard_constraints`,
`existing_plan` flag.

## Step 2 — Check for active injuries first

If `active_injuries` is non-empty, they **override any other consideration**.
State them explicitly in the rationale and let them shape the plan (e.g.,
sport rebalance, reduced run volume, strength-heavy anatomical adaptation).
If the active flag makes the declared goal infeasible in the available
runway, say so — do not paper over it.

## Step 3 — Retrieve methodology

Use the `coach-db` MCP `search_knowledge` tool with queries tuned to the goal
kind. Example queries:

- Race, Ironman: `"ironman base phase structure volume progression"`,
  `"ironman taper length and intensity"`
- Race, 70.3: `"half ironman 16 week build periodization"`
- Non-race, FTP target: `"FTP progression training blocks 3 week"`
- Rolling block: `"base phase aerobic capacity z2 structure"`

Prefer snippets with `credibility: peer_reviewed` or `expert_practitioner`.
Flag any `unvetted` snippet you use.

## Step 4 — Compose the phase chain

Use `tempo.composition.compose_for_goal` rather than picking a template
by hand. It accepts a typed `tempo.goals.Goal`, picks the right template
from `knowledge/methodology/phases.yaml` based on `goal.type` + (for
non-race goals) `goal.metric`, and returns a typed `PhaseChain` valid
against every HARD composition rule.

```python
from tempo import composition, goals, athlete

match = athlete.find_goal(goal_id)              # GoalMatch from goals.yaml or race-calendar.yaml
goal = goals.from_match(match)                   # typed Goal — race | performance_target | maintenance | …

chain = composition.compose_for_goal(
    goal,
    # Map brief['active_injuries'] descriptions into known type tags:
    # 'BSI', 'stress_fracture', 'calf_strain', 'achilles', 'plantar_fasciitis',
    # 'itbs', 'lower_back'. See _INJURY_PRECONDITION_BY_TYPE in composition.py.
    active_injury_types=composition.injury_types_from_flags(brief["active_injuries"]),
)
```

`compose_for_goal` covers two anchor shapes:

- **Race anchor** — `race-calendar.yaml` entry with `date` + `distance`.
  Picks the matching distance template (5K, half-marathon, marathon,
  gran fondo, road race, masters swim meet, Olympic, 70.3, IM). Chain
  ends in `taper_*`.

- **Performance-target anchor** — `goals.yaml` entry with
  `type: performance_target` + `metric` + `target` + `by_date`. Picks
  the metric-specific template:

  | metric | template |
  |---|---|
  | `ftp_w` | `ftp_target_16wk` (base → ftp_progression → vo2_polarisation → deload_test) |
  | `css_pace` / `css_pace_s_per_100m` | `css_target_12wk` |
  | `squat_1rm_kg` / `deadlift_1rm_kg` | `strength_peak_12wk` |

  Chain ends in `deload_test` (1-2 wk freshen + measurement). The "must
  end in taper" rule does NOT apply — non-race goals don't need a
  performance-day taper.

  An unsupported metric raises `CompositionError` listing the supported
  set — surface that to Sean rather than picking a generic template.

- **Maintenance anchor** — `type: maintenance` (with or without
  `by_date`). Routes to `base_building_8wk` (dated) or
  `rolling_base_block_12wk` (open-ended).

Active injuries that map to `active_injury_no_impact` (e.g. BSI, stress
fracture) automatically prepend `rehab_bike_only` + an appropriate
`return_to_*` phase to multisport / run-anchored chains.

Cancelled races (`status: cancelled` in race-calendar.yaml) are filtered
out before goal selection — they cannot anchor a plan and `find_goal`
plus `selectable_races` already exclude them. If two or more `confirmed`
A-races sit within 8 weeks of the chosen goal, `compose_for_goal` raises
`CompositionError` unless `multi_a=True` is passed. The multi-A composer
itself is deferred — surface the collision to Sean and ask which race to
sub-peak rather than force the flag.

If the runway exceeds the template, the composer extends the earliest
base phase rather than diluting build/peak. If a distance / metric
isn't covered, it raises `CompositionError` — surface this to Sean
and recommend either choosing a supported metric, picking a closer
distance, or filing a ticket to add a template entry.

Use the returned `chain.phases` to populate `plan.yaml`'s `phases:`
block. The composer fills in `intensity_distribution`, `weekly_tss_per_hour`,
`key_sessions`, and `sport_focus` from the library, so you don't have to
restate them.

## Step 5 — Compose the artifacts

Decide a `plan-id`. Convention: `<YYYY>-<goal-slug>`, e.g.
`2026-ironman-lake-placid`. For non-race goals, use the goal id directly.

Write to `plans/<plan-id>/`:

### `plan.yaml` — the macro structure

```yaml
plan_id: 2026-ironman-lake-placid
goal_ref: 2026-ironman-lake-placid    # points at race id or goal id
template: ironman_full_24wk            # from phases.yaml (or 'custom')
start_date: 2026-02-09                 # = target_date - total_weeks
target_date: 2026-07-26
total_weeks: 24
weekly_hours_budget: 12                # from preferences.md or goal.constraints

phases:
  - id: anatomical_adaptation
    start_week: 2026-W07
    weeks: 4
    goal: "tissue prep, technique, low-intensity aerobic foundation"
    weekly_tss_target: [350, 450]      # ramp across the phase
    intensity_distribution: { z1_z2: 90, z3: 8, z4_plus: 2 }
    key_sessions: [easy_aerobic_run, easy_aerobic_ride, technique_swim, strength_foundation]
  # ... subsequent phases

race_markers:
  - week_id: 2026-W30
    kind: A                             # A|B|C
    note: "race week — full taper already active"
```

### `rationale.md` — the why

Sections (keep each brief, 3–6 sentences):

1. **Goal framing** — what Sean's training for, priority, constraints.
2. **Framework chosen** — which phases.yaml template, why it fits. Cite the
   knowledge snippets you pulled.
3. **Adaptations** — phase-length compression/extension, sport rebalance for
   injuries, notable departures from the template.
4. **Assumptions** — CTL target trajectory, weekly-hours budget, recovery
   patterns. Be explicit: *"this assumes Sean hits 8 Z2 hours on the bike
   within the first 4 weeks."*
5. **Open questions** — anything you would want to verify with Sean.

### `goal.yaml` — the canonical record

Copy the matching entry from `race-calendar.yaml` / `goals.yaml` into
`plans/<plan-id>/goal.yaml`. This is the frozen snapshot the plan was built
against — if the source is edited later, re-running bootstrap-plan picks up
the change and appends a changelog entry.

## Step 6 — Append the changelog

Write `plans/<plan-id>/changelog.md`. If the file exists, append; otherwise
create with a header. Every entry gets an ISO date, a scope, and a reason.

First run:

```markdown
# Plan Changelog

## 2026-04-24 — plan created
- Template: ironman_full_24wk
- Runway: 24 weeks to target (2026-07-26)
- Notes: compressed build by 1 week to leave 2 weeks of taper
```

Re-run (goal changed):

```markdown
## 2026-05-15 — restructure: target date moved +4 weeks
- New target: 2026-08-23
- Added 2 weeks to base phase, 1 to build, 1 to peak
- Rationale: leverage extra runway for bike durability (limiter flagged in profile)
```

## Step 7 — Log the decision

Call the `coach-db` MCP `log_decision` tool:

```
scope: plan:<plan-id>
kind: plan             # or 'adjust' on a re-run
rationale: <one-paragraph summary suitable for future retrieval>
changed_files: ["plans/<plan-id>/plan.yaml", "plans/<plan-id>/rationale.md", "plans/<plan-id>/goal.yaml", "plans/<plan-id>/changelog.md"]
```

This is mandatory. It's how `search_memory` will find *"why did we pick a
24-week template for Lake Placid"* six months from now.

## Step 8 — Do NOT

- Do **not** fill in weekly sessions. That's `plan-training-week`'s job.
- Do **not** push anything to intervals.icu.
- Do **not** commit the files — Sean reviews the diff first.
- Do **not** skip the changelog or the log_decision call.
- Do **not** pick a template that ignores active injuries.
- Do **not** silently compress more than 20% of the template's weeks without
  explicitly flagging the compression in the rationale and recommending the
  user consider moving the target date.

## Re-run semantics

If `existing_plan` is true in the brief, you are amending, not creating.
Read the existing `plan.yaml` (for context) and the existing `changelog.md`
(to understand history), then:

- Update `plan.yaml` with the new structure.
- Overwrite `rationale.md` *iff* the framework choice changed. Otherwise
  append a "## Restructure YYYY-MM-DD" section at the bottom.
- Append a new changelog entry with kind `restructure`.
- `log_decision` kind is `adjust`, not `plan`.

## Open-ended goals (no target date)

Pick `rolling_base_block_12wk` from phases.yaml (brief's
`applicable_phase_template` will already suggest it). Set
`plan.yaml:total_weeks: 12` with review checkpoints every 4 weeks. The
rationale should say the plan is open-ended and what would trigger a
restructure (e.g., new race declared, FTP target hit early).

## Output summary for the user

At the end, print a short summary: template chosen, total weeks, first-week
date, taper length, any injury-driven adaptation. Invite Sean to review the
diff before committing.
