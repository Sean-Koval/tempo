---
name: midpoint-review
description: Codified mesocycle progress check — "am I on track?" answered consistently from the same evidence base every time, regardless of what the agent remembers to query.
trigger: /midpoint-review [--week YYYY-Www] [--plan-id <id>]
---

# Skill: midpoint-review

Story 05 motivates this: at week 8 of 16 (or any mid-phase moment) the
"am I on track?" question is currently answered freeform — quality
depends on whether the agent remembers to call `get_load_curve`,
`get_adherence`, `compare_plan_to_actual`, threshold provenance, etc.
This skill assembles the same brief every time so reviews are
reproducible and comparable across plans.

**This skill produces a written review, not a plan adjustment.** It
ends with a recommendation + ≥2 alternative options. Sean decides
which path to take; subsequent `/plan-training-week` runs implement the
chosen direction.

## Step 1 — Run preflight

```bash
uv run python .claude/skills/midpoint-review/preflight.py [--week YYYY-Www] [--plan-id <id>] --pretty
```

Reads the JSON brief from stdout. Defaults to the ISO week of today.
Exits 2 if no plan is found or the requested week falls outside any
phase (in which case surface the error and stop — don't guess).

The brief contains:

- `plan` — `phase_id`, `week_in_phase`, `weeks_remaining_in_phase`,
  `weekly_tss_target_mid`, `sport_focus`, `intensity_distribution`.
- `phase_window` — every elapsed week in the phase.
- `target_vs_actual` — CTL overall (target steady-state vs latest
  actual + delta + delta_pct), TSB latest, per-sport CTLs.
- `adherence_phase` — total planned/completed/skipped, completion_pct,
  TSS ratio, plus `weekly`, `by_sport`, and `by_weekday` rollups.
- `wellness_phase` — first-half vs second-half HRV mean (trend
  delta), sleep mean, RHR mean, low-readiness day count.
- `thresholds` — value, set_at, source, age_days, is_stale per zone.
- `calibration_debt` — same DebtItem list the macro dashboard renders.
- `recent_decisions` — last 8 logged decisions in this plan's scope.
- `signals` — deterministic flags (`ctl_below_target_pct_15`,
  `adherence_below_70_consec_2`, `hrv_declining_phase`,
  `stale_threshold:run_threshold_pace`, …) — your evidence base.

## Step 2 — Read the brief once, then write the review

Don't reach for additional MCP tools unless the brief explicitly
points at a gap. The whole point of the skill is consistency: every
midpoint review reasons over the same evidence shape. If you find
yourself wanting more data, log it as a follow-up rather than
expanding the brief inline.

## Step 3 — Hypothesis: name 1–2 dominant deviations

Walk the `signals` list. Group them into causes:

- **Stale calibration** — any `stale_threshold:*`. The actual fitness
  may match plan, but zones are anchored to old tests, so sessions
  feel mis-targeted (too easy or too hard). Cheap to fix: schedule a
  test in the next plan-training-week draft.
- **Adherence gap** — `phase_completion_below_70`,
  `adherence_below_70_consec_2`, `tss_under_target`. The plan asked
  for more than was delivered. Could be schedule-driven, illness,
  motivation, or under-budgeted weekly hours.
- **Real fitness gap** — `ctl_below_target_pct_15` *combined with*
  high adherence and `tss_under_target` low. The plan's TSS target
  was wrong; ramp is producing less CTL than modelled. Often points
  back to threshold staleness or a too-aggressive phase template.
- **Recovery deficit** — `hrv_declining_phase`,
  `low_readiness_pattern`. Wellness is trending wrong direction; even
  if numbers look on-target, the cost is rising.
- **Overcooking** — `ctl_above_target_pct_15`, `tss_over_target`. Too
  much work is being done; risk of injury / TSB-too-negative spike.

Pick the 1–2 *dominant* deviations, not all of them. State the
evidence in plain numbers: "CTL 38, target 45, ratio 0.84".

## Step 4 — Options: at least 2 reasoned alternatives + a recommendation

Format every option as:

```markdown
### Option N — <short label>
- **What:** <concrete adjustment in 1–2 sentences>
- **Why it fits the data:** <which signals it addresses>
- **Tradeoff:** <what you give up>
- **Cost:** <effort, risk, time-to-evidence>
```

Typical options for the dominant-cause categories:

| Cause | Common options |
| --- | --- |
| Stale calibration | Schedule a 5K TT / FTP test next week + recalibrate before next plan-training-week. |
| Adherence gap | Drop weekly_tss_target_mid 10%, or restructure long-day pattern, or escalate `bootstrap-plan` to renegotiate phase template. |
| Real fitness gap | Same as adherence gap *plus* extend phase by 1–2 weeks if calendar permits. |
| Recovery deficit | Insert a deload week + drop intensity-distribution Z3+ share. |
| Overcooking | Cap ramp at +3 CTL/wk, swap a key session to Z2. |

End with **one** "Recommendation:" line that names the option you'd
choose and why. Sean is the decider — the recommendation is
information, not authority.

## Step 5 — Write the review file

Write `plans/<plan-id>/reviews/midpoint-<week_id>.md` (the brief's
`review_path` is the resolved path). Overwrite if it already exists —
midpoint reviews are idempotent on re-run. Format:

```markdown
---
plan_id: 2027-half-ironman
phase_id: aerobic_base_1_volume
week_id: 2026-W30
week_in_phase: 4
weeks_remaining_in_phase: 5
generated_at: 2026-04-29
---

# Midpoint review — phase aerobic_base_1_volume, week 4 of 9

## Where we are vs plan

| Metric | Target | Actual | Delta |
| --- | --- | --- | --- |
| CTL (steady-state) | 47.1 | 39.8 | -7.3 (-15.5%) |
| TSB latest | — | -8 | — |
| Adherence (phase) | 100% | 78% | -22pt |
| TSS ratio (actual / planned) | 1.00 | 0.81 | -0.19 |

## Threshold provenance

| Zone | Value | Set | Source | Age | Status |
| --- | --- | --- | --- | --- | --- |
| ftp_w | 245 W | 2026-04-15 | field_test | 14d | fresh |
| run_threshold_pace | 4:42/km | 2025-12-01 | race_result | 149d | **stale (>90d)** |
| swim_css_pace | 1:42/100m | 2026-01-20 | field_test | 99d | fresh |

## Hypothesis

[1–2 paragraphs naming dominant deviations + supporting numbers
from `signals` and the brief.]

## Options

### Option 1 — <label>
[...]

### Option 2 — <label>
[...]

## Recommendation

<One line: which option, why, and what evidence to re-check in 2 weeks.>

## Decisions referenced

- 2026-W26 plan-week — pulled Wednesday hard because HRV down (decision id 142)
- ...
```

## Step 6 — Log the decision

Call the `coach-db` MCP `log_decision` tool:

```
scope: plan:<plan_id>:midpoint:<week_id>
kind: review
rationale: <one paragraph: dominant deviation + recommended option, naming the signals that drove it>
changed_files: ["plans/<plan-id>/reviews/midpoint-<week_id>.md"]
```

Re-running the skill on the same week overwrites the review file but
appends a new decision row. That's intentional: the decisions table
becomes the audit trail of "how did we read this phase the first
time vs the second time?" — useful when looking back six months later.

## Step 7 — Do NOT

- Do **not** push to intervals.icu. This skill is read-only against
  the calendar.
- Do **not** modify `plan.yaml` from here. If the recommendation is
  "restructure", surface it and run `/bootstrap-plan` separately.
- Do **not** invent metrics that aren't in the brief — the brief
  shape is the contract.
- Do **not** skip the `log_decision` call.
- Do **not** override an active injury constraint in any option —
  R-5 in `decision-rules.md` is HARD.

## Output summary for the user

At the end, print:

- Phase + week-in-phase + weeks remaining.
- Dominant deviation in one sentence.
- Recommended option in one sentence.
- Path to the review file.
- Reminder that the review is advisory; next step is Sean's call.
