# 01 — First marathon, 18-week build

## Persona

**Maya**, 34, intermediate runner. Has done several halves (2:05 PR), wants to
run her first full marathon in October with a soft 4:15 target. Trains 5x/wk,
no current injuries. Uses a Garmin watch synced to Strava → intervals.icu.
Lives in a hilly suburb; access to a track once a week.

## Goal

Bootstrap an 18-week build, see what each week looks like, push it to her
calendar, adjust as life happens, arrive at race day with a paced plan.

## Step-by-step

### 1. Cold start — Tempo isn't aware of her yet

Maya clones tempo and reads README. She fills in:

- `athlete/profile.yaml` — sex/age/weight, recent FTP-equivalent run threshold
  (vDOT 42 from her latest 10K), max HR, LTHR, and PRs.
- `athlete/preferences.md` — Mon/Wed/Fri/Sat/Sun running, Sat long run, Sun
  recovery; no swim/bike; quality session limit 2/wk; cap weekly hours at 7.
- `athlete/race-calendar.yaml`:

  ```yaml
  - id: chicago-2026
    date: 2026-10-11
    type: marathon
    priority: A
    location: Chicago, IL
    target: "4:15:00"
    notes: "First full. Goal is finish strong, not blow up."
  ```

- `athlete/injury-log.md` — empty Active section, two historical entries.

She runs `coach sync` to pull her last 90 days of intervals data.

> **GAP — onboarding wizard.** She did all of that by reading docs and
> editing YAML. A `coach init` flow that walks the file creation, validates
> field-by-field, and offers sensible defaults from intervals.icu would
> shave hours off cold start. She bounced off "vDOT" and had to look it up.

### 2. Verify the system is talking to intervals

```
coach doctor
```

Output (illustrative):
```
intervals.icu        OK   athlete i12345, last activity 2026-04-26
coach.db             OK   2,401 activities, 4,612 wellness rows
embedding model      OK   BAAI/bge-small-en-v1.5
active plan          WARN no active plan in plans/
```

The active-plan warning is expected — she hasn't bootstrapped yet.

### 3. Bootstrap the plan

She invokes the slash command:

```
/bootstrap-plan
```

Agent reads `athlete/`, finds Chicago 2026 as the only A-race, computes
a 24-week runway from today (2026-04-27 → 2026-10-11). With the new
composition library it picks the **`marathon_18wk`** template, prepends a
6-week extension `aerobic_base_1_volume` block to absorb the runway, and
asks her to confirm before writing.

Files produced under `plans/2026-chicago-marathon/`:
- `goal.yaml` — A-race + target time + pacing strategy.
- `plan.yaml` — 24-week chain: base extension → base 1-3 → build 1-2 → peak →
  taper → race week.
- `rationale.md` — why this chain, the runway tradeoff, the assumed CTL ramp.
- `changelog.md` — entry: "plan created from chicago-2026 race target."

### 4. Macro-view sanity check

```
/coach-dashboard-macro
```

Renders an HTML Gantt with current-week marker, calibration debt panel
(none for her — full profile, recent data), and the phase chain. She sees
where the long-run progression lands each week and confirms the peak long
run (32 km) feels right.

### 5. First week drafted

```
/plan-training-week 2026-W18
```

The skill's preflight pulls 14d adherence (~95%), wellness mean, current
CTL (38), the phase template's Mon/Tue/etc. session shape, and the
session library. It drafts 5 sessions:

- Mon — easy 50min Z2
- Tue — track 6×800 @ 10K pace + 400 jog
- Wed — easy 60min hilly
- Sat — long run 18km steady (CTL+2 ramp)
- Sun — recovery 30min

Each is validated against `decision-rules.md` via `validate_week()`.

> **GAP — vague pacing without explicit zones.** The session says "@ 10K
> pace" but Maya's 10K pace is 5:23/km from the field, derived from a 12-month-old
> race. The agent ought to flag "your stored 10K pace may be stale; want
> to re-test in week 2?" Today the rules check zones, not zone freshness.

### 6. Diff and push

She reviews `plans/2026-chicago-marathon/weeks/2026-W18.md`, edits Wed to
swap to a treadmill (rain forecast), and runs:

```
coach push-week 2026-W18 --dry-run
coach push-week 2026-W18
```

Five tagged events show up in intervals.icu, ready for her watch.

### 7. Wednesday — life intervenes

She tweaks her calf during Tuesday's track session and skips Wednesday.
Adds to `journal/2026-04-29.md`: "Tuesday's last 800 felt sharp on the
right calf, mild soreness Wed AM, took rest day instead."

She also adds a temporary entry to `injury-log.md` Active section:

```
- ## Right calf, mild — 2026-04-29
  Probably overcooked Tue track. Watching. Run only if 0/10 today.
```

### 8. Mid-week adjustment

Friday morning:
```
/morning-check-in
```

Wellness in: HRV down 8%, soreness 3/10 on calf, sleep 7.5h.
Agent reads journal + active injury, and prompts:

> "Your Sat long run is 18km. With the calf flag and an 8% HRV drop,
> options: (a) cap at 14km, easy throughout — SOFT R-15 override,
> (b) shift to Sunday and skip recovery, (c) swap to 60min Z1 elliptical.
> Which?"

She picks (a). Agent edits `weeks/2026-W18.md`, appends rationale, calls
`log_decision`. **[GAP — no `coach amend-session` CLI yet]**: today this
takes a careful agent prompt and a manual `git diff` review. An atomic
"shorten-session-with-rationale" command would make this a one-liner.

### 9. Sunday — week review

```
/review-week
```

Agent compares planned vs actual, surfaces:

- 4/5 sessions completed, calf-driven 14km Z1 instead of 18km steady.
- Adherence ratio: 78% by TSS, 80% by duration.
- Wellness trend: HRV recovered Friday, neutral Sat-Sun.
- Recommendations for W19: keep volume flat, add a 1-mile pace probe to
  re-establish 10K pace if calf is clear.

### 10. Race-week mode (week 17 of 18)

```
/draft-race-plan
```

Builds a 4-week countdown:
- Taper structure (volume −30/−50/−70%).
- Race-day fueling (60-90g carbs/hr, 2 gels pre, 4 gels on course, salt
  every 45 min if temp > 18°C).
- Pacing strategy: even splits 6:00/km → 4:13:30, two-pace fade tolerance.
- Mental cues: "fast and patient through 30km, then it's a 12K race."
- Contingencies: weather (heat → pace +5s/km, cold → no change), GI distress
  protocol, hitting the wall.

She reviews, edits the mental-cue language, and saves.

### 11. Race day — and after

She finishes 4:11:42. Logs to `journal/2026-10-11.md`:
> "Even splits through 32, drift to 6:08 last 8K, finished feeling like I
> could have given 30s more. Fueling went perfectly, no GI."

Two days later she runs `/review-week` on race week and tells the agent
"new goal — sub-4 in spring 2027, want to incorporate Pfitz 18/85."

> **[GAP — methodology selection].** Today bootstrap-plan picks from
> Tempo's templates. Maya wants to use a specific external methodology
> (Pfitz 18/85, Hanson, Daniels). The agent could ingest the methodology
> from `knowledge/methodology/` if it's been added — but there's no
> "import this plan structure" flow. She'd have to translate Pfitz's
> table into `phase_library.yaml` herself.

## What works today

- 18-week marathon template exists in the composition library.
- Phase composition handles runway extension automatically.
- Decision rules catch HARD violations.
- Calibration debt would have flagged stale FTP/threshold (would surface
  the stale-10K-pace concern with a small extension to detect "pace tests
  older than 90 days").
- Push to intervals is idempotent.

## Gaps surfaced

1. **Onboarding wizard** — no `coach init` for cold start. (US-3 flagged
   calibration debt, but that surfaces *after* a plan exists; users need
   pre-plan validation too.)
2. **Stale-test detection** — calibration debt covers missing FTP; it
   doesn't know when paces or HR thresholds were last set or *how stale they
   are*. Should be a debt category.
3. **External methodology import** — Pfitz, Hanson, Daniels, Magness,
   80/20 Running each have specific phase shapes. The composition library
   covers generic shapes; named methodologies require manual translation.
4. **Atomic session-amendment CLI** — `coach amend-session 2026-W18 sat
   --duration 14km --zone z1 --reason "calf flag"`. Manual edits +
   `log_decision` calls today.
5. **Mobile capture** — Wednesday/Friday morning entries to `journal/` +
   `injury-log.md` need a non-terminal path. A simple Shortcuts/iOS
   workflow that POSTs to a local endpoint would close this.
6. **Race-day weather integration** — `/draft-race-plan` mentions weather
   contingencies but doesn't fetch or watch the forecast. A `coach
   race-weather chicago-2026` command + dashboard panel would help.
7. **Goal continuity** — after race day, "what's next" is freeform. A
   `coach plan succession --previous chicago-2026 --next sub-4-2027` would
   carry forward learnings, current CTL, and pacing reality automatically.
