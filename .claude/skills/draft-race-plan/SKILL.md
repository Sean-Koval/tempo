---
name: draft-race-plan
description: Draft a 4-weeks-out race countdown — taper, pacing, race-week nutrition, contingencies, mental cues. Grounded in athlete-tested.yaml > literature.
trigger: /draft-race-plan [<race-id>]
---

# Skill: draft-race-plan

You are drafting Sean's race-day execution plan. The race is in roughly four
weeks; structural training is mostly done. Your job is to commit the
five-section plan that turns months of training into a single race-day
performance: **taper, pacing, race-week nutrition, contingencies, mental
cues.**

**This skill drafts execution, not training.** Don't restructure phases.
Don't push more volume. The training is what it is — your job is to
deliver it cleanly to the start line and back to the finish.

## Step 1 — Run preflight

```bash
uv run python .claude/skills/draft-race-plan/preflight.py [--race-id <id>]
```

If `--race-id` is omitted, the script auto-picks the next A-priority race
within 28 days of today. If that returns nothing, surface the error — Sean
either doesn't have a race coming up or it's outside the planning window.

The brief contains: `race` (id/title/date/distance/location/conditions),
`days_until_race`, `weeks_until_race`, `plan` (peak_phase + taper_phase
references), `athlete_state` (FTP, threshold pace, weight), `active_injuries`,
`hard_constraints`, `recent_load` (peak CTL/ATL/lowest TSB across last 8
weeks), `last_8wk_adherence`, `nutrition` (full athlete-tested.yaml entries
+ tolerated/failed product summary + max tolerated g/hr), `race_plan_path`,
`race_plan_exists`.

## Step 2 — Active injuries override

If `active_injuries` is non-empty, they override race-day plans the same way
they override weekly plans (rule R-5, HARD).

- If a flag makes the race **unsafe** (acute structural injury, illness),
  say so directly and stop. Don't draft a "carefully managed" race plan
  around an injury that needs healing.
- If the flag is manageable (mild tendon flare, residual fatigue), state
  the constraints in the contingency section and adjust pacing accordingly.

## Step 3 — Pull supporting knowledge

Use `coach-db` MCP `search_knowledge` for any of the following the agent
needs to cite:

- `"race-week carbohydrate loading"` → `knowledge/nutrition/race-week-carb-load.md`
- `"in-race fueling g/hr ironman"` → `knowledge/nutrition/session-fueling.md`,
  `race-day.md`
- `"gut training progression"` → `knowledge/nutrition/gut-training.md`
- `"taper intensity volume reduction"` → from research/ if available
- `"ironman race pacing power normalized"` → from research/

Cite the source file in any claim that comes from literature. Athlete-tested
entries override literature wherever they conflict.

## Step 4 — Compose the five sections

Write to `plans/<plan-id>/race-day-plan.md`. If the file already exists,
**do not overwrite** — append a `## Revision YYYY-MM-DD` section below the
existing content with a one-paragraph summary of what changed and why.

### Frontmatter

```yaml
---
race_id: <from brief.race.id>
race_date: <from brief.race.date>
plan_id: <from brief.plan.plan_id>
drafted: <today YYYY-MM-DD>
days_until_race: <from brief>
---
```

### 1 — Taper shape

- **Which weeks:** Race-week and the prior 1–3 depending on race distance
  (full IM = 3-week taper; 70.3 = 2-week; shorter = 1-week).
- **Volume reduction:** ~50% race-week, ~25% prior week, ~10% the week
  before. Cite `plan.taper_phase` if the plan defines one.
- **Intensity preservation:** keep one short race-pace touch each week —
  taper is about volume reduction, not detraining. Drop frequency before
  intensity.
- **Sport balance:** swim freshness recovers fast (1–2 days); bike and run
  need the full taper.

### 2 — Race-day pacing

Compute targets from `athlete_state`:

- **Bike:** target intensity factor (IF). Full IM = 0.68–0.72 of FTP for
  most athletes; 70.3 = 0.78–0.84. Multiply: `target_w = ftp_w × IF`.
  Provide a 3-zone pacing range, not a single number. State the HR ceiling
  (cap at zone 3 lower-end for full IM).
- **Run:** off-bike pace tied to `run_threshold_pace`. Full IM = +30 to
  +60 sec/mile slower than threshold; 70.3 = +10 to +20 sec/mile.
- **Swim:** target pace per 100m from `swim_css_pace`. Steady, not hard —
  swim is the lowest-leverage discipline in IM.
- State the pacing rule explicitly: "first hour feels easy, second hour
  feels right, third hour is the test." Pacing is a moral problem, not a
  math problem — most failures are pacing failures dressed up as nutrition
  failures.

### 3 — Race-week nutrition

- **3-day carb-load math** (cite `race-week-carb-load.md`):
  - Days -3 to -1: target g/kg/day × athlete weight.
  - Day -1: low fiber, light dinner, last serious eating window.
- **Race-morning breakfast:** 3–3.5 hr pre-start, ~150 g carbs, low fat/
  fiber/protein. Use only items appearing in `athlete-tested.yaml`'s
  tolerated list — race day is not the day for novelty.
- **In-race g/hr targets:**
  - **Bike:** target = MIN(plan target, `nutrition.tolerated_max_carbs_g_per_hr`).
    State which one is the binding constraint.
  - **Run:** ~75% of bike target (gut blood flow drops with vibration —
    cite `session-fueling.md`).
- **Sodium + fluid:** based on expected conditions in `race.expected_conditions`.
  500–1000 mg/hr sodium baseline; 1000–1500 mg/hr in heat.
- **Caffeine:** 3 mg/kg pre-race + 100–200 mg total split across the run.
- **Cite ≥2 entries from `nutrition.entries` (athlete-tested.yaml) by date
  or session_type.** This is a hard requirement — the plan must show its
  empirical footing, not just literature.

### 4 — Contingency

One-paragraph response per scenario. Pre-planned, so it doesn't have to be
invented at hour 7:

- **Heat:** ice in cap + tri-suit, slow bike pace 5–8 W, walk first 30 sec
  of every aid station on run.
- **Rain / cold:** arm sleeves on bike, increase carb intake (gut tolerates
  more in cool conditions), watch for hypothermia post-swim if water cold.
- **GI trouble:** drop intensity 10%, switch to dilute fluid carbs only,
  walk aid stations. Resume normal protocol when symptoms clear.
- **Mechanical (bike):** flat repair plan, what to do if shifting fails,
  when to abandon. Carry: tube, CO2, multitool, chain link.
- **Pacing dread (the wall):** mental cue from section 5; switch to micro-
  goals (next aid station, not next 5k).

### 5 — Mental cue sheet

4–6 one-line cues, each tied to a race segment. Examples (replace with
something honest about Sean's actual mental patterns):

- **Swim start:** "Slow is smooth, smooth is fast."
- **Bike hour 1:** "If it feels easy, it is easy. Don't fix it."
- **Bike hour 4 (the dark middle):** "Eat. Drink. Trust the work."
- **T2 / run start:** "Two miles to find your legs. Don't judge yet."
- **Run mile 13:** "This is the work. The work is now."
- **Run mile 22:** "One aid station. Then the next one."

## Step 5 — Append to changelog

Append to `plans/<plan-id>/changelog.md`:

```markdown
## YYYY-MM-DD — race-day plan drafted for <race-id>

- Race: <title>, <date>, <distance>
- Days out: <N>
- Bike target IF: <X> ({{X}} × {{ftp}} = {{Y}}W normalized)
- Run target pace: <Y> /mi off-bike
- Race-day g/hr target: <Z> (binding constraint: literature ceiling | athlete-tested ceiling | gut-training threshold)
- Key assumptions: <one paragraph — what we're betting on, what could go wrong>
```

## Step 6 — Log the decision

Call `coach-db.log_decision`:

- `scope`: `race:<race-id>`
- `kind`: `race_plan`
- `rationale`: one paragraph — what the plan commits to and the single most
  important assumption (e.g. "g/hr ceiling at 80g, set by athlete-tested
  entry 2026-03-15; literature supports 95g but gut hasn't been tested
  there at race-pace").
- `changed_files`: `[plans/<plan-id>/race-day-plan.md, plans/<plan-id>/changelog.md]`

## Invariants (HARD — do not bend)

- **`athlete-tested.yaml` outranks literature.** If literature supports
  90 g/hr but `nutrition.tolerated_max_carbs_g_per_hr` is 70, race-day is
  70. Flag the ceiling explicitly in the nutrition section. Do not draft
  "let's try 80 on race day" — race day is not the day to test.
- **Gut-training gate (R-18):** race-day g/hr must have been successfully
  tolerated in **3+ race-sim sessions at race-pace intensity**. Count
  matching entries in `nutrition.entries`. If fewer than 3, flag HARD
  in the nutrition section and recommend ceiling at the highest 3-session-
  validated intake. Do not paper over.
- **Active injuries still apply.** Rule R-5 (HARD) — never overrride.
- **Don't overwrite an existing race plan.** Append a `## Revision` section
  with reasons for the change.

## Verification

- `/draft-race-plan <race-id>` produces `plans/<plan-id>/race-day-plan.md`
  with all 5 sections.
- Nutrition section cites at least 2 athlete-tested entries by date.
- Re-running produces a `## Revision` section, not an overwrite.
- A new entry appears in the `decisions` table with `kind=race_plan`.
