---
name: plan-training-week
description: Draft the next training week's sessions against the plan's phase template, grounded in 14d adherence/wellness/load and decision-rules.md.
trigger: /plan-training-week [--week YYYY-Www]
---

# Skill: plan-training-week

You are drafting one training week for Sean. The macro structure already
exists in `plans/<plan-id>/plan.yaml` (from `bootstrap-plan`). Your job is
to fill in this week's sessions — 5–9 of them — grounded in recent adherence,
wellness, and load, and validated against `decision-rules.md`.

**This skill drafts sessions, not structure.** Never touch `plan.yaml`'s
phases from here — if the plan is off-track by >2 weeks, stop and recommend
re-running `bootstrap-plan`.

## Step 1 — Run preflight

```bash
uv run python .claude/skills/plan-training-week/preflight.py [--week YYYY-Www]
```

Reads the JSON brief from stdout. Defaults to next ISO week if `--week` is
omitted. If the script exits non-zero (no plan found, ambiguous plan_id,
missing files), stop and surface the error to the user — don't guess.

The brief contains: `week_id`, `plan`, `ctl_drift`, `recent_load_14d`,
`readiness`, `recent_adherence` (last week), `prior_adherence` (2 weeks ago),
`recent_weekly_tss`, `adherence_patterns`, `active_injuries`,
`hard_constraints`, `athlete_state`, `week_already_drafted`.

## Step 2 — Macro-drift check

If `ctl_drift.delta_ctl_vs_plan` is strongly negative (roughly < -10, or
equivalently >2 weeks behind the planned steady-state CTL), **stop**. Surface
the number, explain that macro drift shouldn't be papered over with a week
patch, and recommend running `/bootstrap-plan <goal-id>` to restructure.

A small drift (±5 CTL) is normal — continue, but note it in the changelog.

If `week_already_drafted` is true, read the existing week file first and
treat this as an amendment rather than a new draft.

## Step 3 — Active injuries override

If `active_injuries` is non-empty, they **override any other consideration**:

- No sessions that violate listed constraints (e.g. no >Z3 run while calf
  strain is active).
- Substitute equivalent TSS from non-affected sports where possible.
- State the affected sessions and the substitutions in the changelog.

This is enforced by rule **R-5 (HARD)** in `decision-rules.md`.

## Step 3b — Honor `adherence_patterns`

Read `adherence_patterns` (8 weeks, all weekday/sport/session-type/context
buckets z-scored against the overall completion rate). Two cases:

- `status: "insufficient_data"` — < 8 weeks of session history. Skip this step.
- `status: "ok"` and `signals` non-empty — each entry is a systematic
  drop-off. Treat each as a soft constraint:

| Dimension | Adjustment |
| --- | --- |
| `weekday: <day>` | Don't put a HARD-quality session on `<day>`. Move it ±1 day or swap with a Z2/recovery slot. |
| `sport: <sport>` | If feasible, slightly under-budget that sport this week — do the higher-completion sport for the substitute TSS. Surface in the changelog. |
| `session_type: <type>` | Watch — if the missed type was load-critical (e.g. `long_ride_endurance`) flag it; consider reducing target TSS or rescheduling to a higher-completion day. |
| `context: travel_week` | If this week is itself a travel week (check `journal/` and calendar), drop weekly_tss_target_mid 15-20% and rely on short skip-tolerant sessions. |

The note in the changelog must name the signal verbatim
(`"Thu adherence 50% vs 88% baseline (8 sessions)"`). Without that, future
sessions can't audit why the week was shaped this way.

Pattern signals are advisory — they don't override `decision-rules.md` HARD
rules or `active_injuries`. If both conflict, the HARD rule wins.

## Step 4 — Read the current phase template

From the brief's `plan.phase`:

- `id` / `goal` / `intensity_distribution` — shape this week around these.
- `weekly_tss_target_mid` — target total TSS for the week. Adjust ±10% based
  on wellness + adherence signals from the brief.
- `key_sessions` — library ids to anchor the week around. Prefer these over
  inventing.

Also read the per-sport file(s) under `knowledge/methodology/session-library/`
for each library ref's duration/TSS/structure ranges. The library is
split by sport — pick the file(s) that match this phase's
`sport_focus`:

| Phase `sport_focus` weight ≥ 0.15 | File to read |
| --- | --- |
| `swim` | `knowledge/methodology/session-library/swim.md` |
| `bike` | `knowledge/methodology/session-library/bike.md` |
| `run`  | `knowledge/methodology/session-library/run.md`  |
| `strength` | `knowledge/methodology/session-library/strength.md` |
| any phase whose `key_sessions` contains a brick ref (`brick_run_off_bike`, `race_sim_brick`, `race_day_fueling_rehearsal`) | `knowledge/methodology/session-library/brick.md` |

Skip files for sports the phase doesn't touch — a `peak_run` phase has
no business pulling swim sprint sets. This filtering is the whole
point of the split (per `tempo-208`); the composer already filters
`key_sessions` by sport, the skill should mirror it.

Pick point values based on phase + this week's position within the
phase (`plan.week_of_phase`).

If you need a session archetype that isn't in the library, call
`coach-db.find_similar_session(description=..., sport=<sport>)` first
— always pass `sport` to keep the match within the same sport bucket.
If a library entry is semantically close, use it rather than inventing.
A truly novel session needs an explicit changelog justification.

## Step 5 — Validate every drafted session

Before committing a session, check it against
`knowledge/methodology/decision-rules.md`:

| Severity | Rule examples | Action on trigger |
| --- | --- | --- |
| **HARD** | R-4 illness-adjacent, R-5 active injury, R-7 bone stress, R-10 TSB-in-peak, R-14 long-run +15%, R-17 fueling rehearsal | Back off the session. Never override. |
| **SOFT** | R-1 HRV trend, R-2 low readiness, R-8 CTL ramp cap, R-9 neg TSB, R-11 back-to-back hard, R-12 long-day anchors, R-15 down-week, R-16 race-pace timing | Can override with a changelog rationale + `log_decision` adjust. |
| **WATCH** | R-3 sleep deficit, R-13 swim-first, R-18 gut training | Flag in changelog; don't block. |

Trigger signals to check from the brief:

- `readiness.hrv_trend_delta` < 0 + latest TSB negative → R-1 may apply.
- `readiness.readiness_latest` < 5 for 2+ recent days → R-2.
- `recent_load_14d[-1].ramp_7d` > 8 → R-8.
- `active_injuries` non-empty → R-5, R-6, R-7 chain.
- For every session > 3h bike or > 90 min run → note fueling plan (R-17).

## Step 6 — Write the week file

Write `plans/<plan-id>/weeks/<week_id>.md`. Each session has a prose
header AND a fenced ```yaml tempo:session block — the prose is human
documentation, the YAML block is what `coach week import` parses into
`sessions_planned` (the table `coach push-week` reads).

Format:

````markdown
---
week_id: 2026-W18
plan_id: 2026-ironman-lake-placid
phase: build
week_of_phase: 1
target_tss: 650
intensity_distribution: { z1_z2: 75, z3: 15, z4_plus: 10 }
---

# Week 2026-W18 — build phase (1/6)

## Sessions

### Monday 2026-04-27 — easy_aerobic_ride
- Target TSS: 55
- Target duration: 75 min
- Purpose: recovery volume
- Notes: keep HR under Z2-cap; no surges.

```yaml tempo:session
id_slug: mon-recovery-spin
date: 2026-04-27
sport: bike
library_ref: easy_aerobic_ride
target_tss: 55
target_duration_s: 4500
purpose: recovery volume
notes: keep HR under Z2-cap; no surges.
```

### Tuesday 2026-04-28 — threshold_run
- Target TSS: 85
...

```yaml tempo:session
id_slug: tue-threshold-run
date: 2026-04-28
sport: run
library_ref: threshold_run
target_tss: 85
target_duration_s: 3600
purpose: threshold work
notes: "15 WU / 4x5min @ thresh w/ 90s easy / 10 CD"
```

## Notes
- Drafted off HRV down-trend signal — pulled Wednesday hard ride → Z2.
- R-11 (SOFT) overridden Thu/Fri because Sat is a full rest day.
````

YAML block schema (option C — per tempo-1q2):

| Field               | Required    | Notes                                                                                                                          |
| ---                 | ---         | ---                                                                                                                            |
| `id_slug`           | yes         | day-abbrev + archetype, e.g. `tue-am-tempo`, `sat-long-ride`. Composed into `<plan_id>/<week_id>/<id_slug>` for the DB row id. |
| `date`              | yes         | ISO `YYYY-MM-DD`.                                                                                                              |
| `sport`             | yes         | bike / run / swim / strength / brick.                                                                                          |
| `library_ref`       | recommended | matches an entry in `knowledge/methodology/session-library/`.                                                                  |
| `target_tss`        | recommended | numeric; omit if novel/strength.                                                                                               |
| `target_duration_s` | recommended | integer seconds.                                                                                                               |
| `purpose`           | optional    | one-line summary; surfaces in `coach push-week --dry-run`.                                                                     |
| `notes`             | optional    | **quote** the value with `"…"` if it contains `:` (YAML parses unquoted `:` as mapping syntax).                                |

Aim for 5–9 sessions. Each session gets a `library_ref` in the body or an
explicit "inline (novel)" label with justification.

After Sean reviews the diff, the bridge to intervals is:

```
coach week import <week-id> --dry-run   # parse + preview diff against the DB
coach week import <week-id>             # UPSERT into sessions_planned
coach push-week <week-id> --dry-run     # preview the intervals push
coach push-week <week-id>               # write to intervals.icu
```

## Step 7 — Append to the changelog

Write/append to `plans/<plan-id>/changelog.md` — one dated section per draft:

```markdown
## 2026-04-24 — drafted 2026-W18
- Phase: build (week 1 of 6)
- Target TSS: 650 (phase mid 675, shaded -4% for HRV down-trend)
- Signals driving shape:
  - HRV 7d mean -6.2 vs prior → R-1 SOFT applied, Wednesday swapped
  - Readiness 6/10 latest → within normal
  - Adherence last week 87% (skipped long-run for travel)
- Overrides: R-11 SOFT (Thu/Fri hard-hard) — justified by Sat rest.
```

## Step 8 — Log the decision

Call the `coach-db` MCP `log_decision` tool:

```
scope: week:<week_id>
kind: plan              # 'adjust' if amending an existing draft
rationale: <one-paragraph summary citing the concrete brief signals>
changed_files: ["plans/<plan-id>/weeks/<week_id>.md", "plans/<plan-id>/changelog.md"]
```

**Mandatory.** This is how `search_memory` surfaces *"why did we pull
Wednesday's intensity in week 18"* six months from now.

## Step 9 — Do NOT

- Do **not** push to intervals.icu. Sean reviews the diff, then runs
  `coach week import <week-id>` followed by `coach push-week <week-id>`
  explicitly.
- Do **not** commit the files — diff first.
- Do **not** touch `plan.yaml`. Macro changes are `bootstrap-plan`'s job.
- Do **not** override a HARD rule. Back off the session.
- Do **not** skip the changelog entry or the `log_decision` call.
- Do **not** silently invent novel sessions when a library entry would fit.
- Do **not** omit the per-session ```yaml tempo:session block — without it,
  `coach week import` has nothing to read and `coach push-week` will say
  "No planned sessions for <week>".

## Output summary for the user

At the end, print a short summary:

- Phase + week-of-phase + target TSS.
- Session count + any sessions substituted/backed-off and why.
- Every applied SOFT override (one line each) + every HARD rule that
  constrained the draft.
- Any macro drift flagged, even if within tolerance.

Invite Sean to diff, then run `coach week import <week-id>` followed by
`coach push-week <week-id>`.
