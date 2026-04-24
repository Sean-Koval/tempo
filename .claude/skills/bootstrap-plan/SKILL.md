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

## Step 4 — Pick and adapt the phase template

- Use `applicable_phase_template` from the brief if present.
- If absent (agent-decide case), pick from `knowledge/methodology/phases.yaml`
  based on goal kind and runway. If runway is shorter than the template's
  `total_weeks`, compress proportionally — explain the compression in the
  rationale.
- If `weeks_until_target` is longer than the template, either extend the base
  phase or add a maintenance block before the template starts. Document
  which and why.

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
