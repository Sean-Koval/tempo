---
name: review-week
description: Post-mortem on a just-completed training week — adherence, wellness trend, load trajectory, lessons, and flags for next week. Feeds the next plan-training-week draft.
trigger: /review-week [--week YYYY-Www]
---

# Skill: review-week

You are producing a post-mortem on a completed week. The week's planned
sessions are in `plans/<plan-id>/weeks/<week_id>.md`. Your job is to write
a review section into the same file, append to the changelog with lessons +
flags, and close the memory loop with `log_decision`.

**This skill reads, reasons, and appends.** Never overwrite the planned
section; the review lives alongside, below.

## Step 1 — Run preflight

```bash
uv run python .claude/skills/review-week/preflight.py [--week YYYY-Www]
```

Reads the JSON brief from stdout. Defaults to last completed week (today - 7d).
If the script exits non-zero (no plan found, ambiguous plan_id, missing
files), stop and surface the error.

The brief contains: `week_id`, `week_start`, `week_end`, `plan` (phase + target),
`adherence`, `deltas` (session-by-session planned vs actual), `per_sport_tss`,
`wellness_trend` (daily), `load_trajectory` (start_ctl → end_ctl + peak_atl
+ low_tsb), `prior_weeks_adherence`, `active_injuries`, `week_file_exists`.

## Step 2 — Synthesize

Produce five sections, each terse and evidence-based. Every claim gets a
number from the brief. No platitudes. "This assumes X" beats pretending
to know.

### 1. Adherence narrative
What landed, what slipped, and the pattern.
- Cite specific deltas: *"Wed threshold_run skipped (reason: travel); Sat
  long_ride_z2 came in at 185 TSS vs 230 planned — 80% of target."*
- Pattern observation: *"Two skipped intensities; aerobic volume held."*

### 2. Wellness signal
Did subjective match objective?
- Cite `wellness_trend`: HRV range, sleep mean, readiness pattern.
- Flag divergences: *"readiness averaged 6/10 but HRV was flat — watch
  for subjective fatigue not showing in HRV."*
- Check HARD rules were honored — if `active_injuries` was present and
  the planned sessions obeyed R-5 constraints, note it; if not, flag.

### 3. Load readout
Did the week hit the plan's CTL trajectory?
- `load_trajectory.start_ctl` → `end_ctl` delta.
- Compare to implied target: `plan.weekly_tss_target_mid / 7` is the
  steady-state daily TSS.
- `peak_atl` + `low_tsb` tell the stress story.
- If off by >5 CTL cumulatively across prior weeks, name it.

### 4. Lessons
1–3 concrete, actionable observations. Not "sleep matters" — *"Thursday's
hard run after 6.1h sleep produced HRV drop and next-day readiness 4 —
move hard days to post-sleep-recovery."*

### 5. Flags for next week
Specific signals the planner should weigh. Each flag gets one line:
- *"CTL drift now -9 vs plan target — re-baseline or hold volume."*
- *"Calf tight during Sat run — add to injury-log if persists 48h."*
- *"Swim volume 60% of target 3 weeks running — recalibrate or cut from plan."*

## Step 3 — Append to the week file

The week file already has the planned section (from `plan-training-week`).
**Append** a `## Review` section below it; do not modify the planned
section or the frontmatter.

```markdown
...existing planned content...

## Review — 2026-04-28

### Adherence
- Completion: 6/7 (85.7%). Planned TSS 600, actual 515 (86%).
- Skipped: Wed threshold_run (travel); shortened: Sat long_ride_z2 (185/230).

### Wellness signal
- Sleep 7-day mean 7.2h; HRV 7-day mean 62 (prior 65, -4.6%).
- Readiness averaged 6.1/10 — soft R-2 territory but not sustained.

### Load readout
- CTL 72.1 → 74.0 (+1.9). Implied target steady-state CTL 96 — currently
  at -22 drift. This is a macro signal, not a week signal.

### Lessons
1. Moved long ride to Sunday worked — Monday easy day absorbed the cost.
2. Hard run under 6.5h sleep (Thu) hurt next-day readiness — respect R-3.

### Flags for next week
- CTL drift widening — recommend /bootstrap-plan rerun before next build wk.
- Swim adherence 60% three weeks — restructure or accept lower CTL_swim.
- Calf tightness Sat run — monitor; add injury flag if >48h.
```

If `week_file_exists` is false, stop and surface: "no planned week file
at plans/<plan-id>/weeks/<week_id>.md — was this week drafted?". Don't
invent adherence data.

## Step 4 — Append to the changelog

Write/append to `plans/<plan-id>/changelog.md`:

```markdown
## 2026-04-28 — review 2026-W17
- Adherence 85.7%, TSS 515/600
- CTL 72.1 → 74.0 (target steady-state 96 — -22 drift, widening)
- Lessons: hard-run-under-fatigue pattern; Sunday long ride works
- Flags for next week: macro drift (recommend restructure), swim adherence, calf watch
```

## Step 5 — Log the decision

Call the `coach-db` MCP `log_decision` tool:

```
scope: week:<week_id>
kind: review
rationale: <one-paragraph summary of adherence narrative + top flags>
changed_files: ["plans/<plan-id>/weeks/<week_id>.md", "plans/<plan-id>/changelog.md"]
```

**Mandatory.** Reviews feed future planning — `search_memory` on
*"lessons from W17"* must hit this entry.

## Re-run semantics

If the week file already has a `## Review` section (detect by scanning for
`^## Review` in the file body), **overwrite** that section and everything
after it, preserving the planned section above. Append a fresh changelog
entry annotated `review updated — <reason>`. `log_decision` kind remains
`review`.

## Do NOT

- Do **not** modify the planned section or frontmatter.
- Do **not** invent numbers. If `adherence.planned_count` is 0, the week
  wasn't drafted or has no DB data — say so explicitly and stop.
- Do **not** skip the changelog or the `log_decision` call.
- Do **not** draft next week's sessions. That's `plan-training-week`.
- Do **not** touch `plan.yaml`. Macro restructures are `bootstrap-plan`.

## Output summary for the user

Print a short summary:

- Completion % + planned/actual TSS.
- Top 1–2 lessons (one line each).
- Top 1–2 flags for next week.
- Any macro-drift recommendation.

Invite Sean to diff, then run `/plan-training-week` when ready.
