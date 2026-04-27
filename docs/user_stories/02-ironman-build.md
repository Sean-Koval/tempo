# 02 — Ironman build, long runway

## Persona

**Diego**, 41, second-year triathlete. Finished IM 70.3 last summer (5:48).
Has signed up for **Ironman Lake Placid 2027-07-25** — a full IM with
substantial climbing on the bike. Currently April 2026, runway is **64 weeks**.
Trains 10-14 h/wk depending on phase. Strong cyclist (FTP 285W), middling
swimmer (1:42/100m CSS), durable runner (3:42 marathon).

## Goal

Build a 64-week macro plan that doesn't burn him out before peak season,
keep momentum through winter, and arrive at Lake Placid bike-strong and
run-durable. This is *the* primary use case the tool was designed for.

## Step-by-step

### 1. Profile + race calendar

Already onboarded — Diego has been syncing for 6 months. His
`athlete/race-calendar.yaml`:

```yaml
- id: lake-placid-2027
  date: 2027-07-25
  type: ironman
  priority: A
  location: Lake Placid, NY
  course_notes: "2x bike loop, 9000ft climbing. Run is rolling, hot July."
  target: "11:45"
- id: lake-george-half-2026
  date: 2026-09-13
  type: ironman_70.3
  priority: B
  notes: "Mid-runway tune-up race. Rust check, not a peak."
- id: half-marathon-tune-2027
  date: 2027-04-18
  type: half_marathon
  priority: C
```

### 2. Bootstrap

```
/bootstrap-plan
```

Composer picks **`triathlon_full_24wk`** as the terminal block, prepends
40 weeks of structured base/durability work using the phase library.
Output chain:

```
weeks 1-12  off_season_general_prep   (Apr-Jul 2026)
weeks 13-20 aerobic_base_1_volume     (Jul-Sep 2026, hits 70.3 in week 18)
weeks 21-32 winter_base_strength      (Sep-Dec 2026, swim focus + bike trainer)
weeks 33-40 aerobic_base_2_threshold  (Jan-Feb 2027)
weeks 41-44 transition_outdoor        (Mar 2027, restart outdoor riding)
weeks 45-46 half_marathon_tune        (Apr 2027, C-race)
weeks 47-58 build_full_specific       (Apr-Jun 2027, IM-pace work)
weeks 59-62 peak                       (late Jun-Jul 2027)
weeks 63-64 taper                      (Jul 2027)
week  65    race_week                  (2027-07-25)
```

`rationale.md` flags: "Lake George 70.3 in week 18 is a B-race; Diego will
*not* taper for it — at most 4 days of reduced volume. Tune-up only."

### 3. Macro dashboard — does the shape make sense

```
/coach-dashboard-macro
```

He sees:
- CTL trajectory: ramp from 65 today → 95 by Sep 2026 (70.3 race),
  consolidate at 90-95 through winter, second ramp to 115 peak by July 2027.
- Sport distribution by phase (bike-heavy in winter, run/brick-heavy in
  build).
- Calibration debt panel: "swim threshold last set 2024-09 → stale, swim
  zones may be off." → he schedules a CSS test in week 2.

> **GAP — multi-A-race seasons.** Lake Placid is the only A. But many
> athletes have a spring goal (Boston) AND a fall goal (Kona). The composer
> can't currently produce a chain with two peaks. He'd have to bootstrap
> separate plans and stitch.

> **GAP — course-specific build.** "9000ft climbing" is in the notes
> field but doesn't shape the plan. The agent doesn't add hill repeats
> or lengthen specific bike intervals to reflect Placid's profile.

### 4. Weekly cadence — same as marathon, but multisport

Each Sunday Diego runs:
```
/review-week
/plan-training-week
```

The plan-training-week skill picks session archetypes from `session-library.md`.
A typical winter week looks like:

- Mon — swim 3000m technique
- Tue — bike trainer 90min sweet-spot 3×15'
- Wed — run 60min easy + strides
- Thu — swim 3500m threshold + bike 75min Z2
- Fri — strength session (deadlift, single-leg, core)
- Sat — long bike trainer 3hr Z2 + 15min off-bike run
- Sun — long run 90min Z2

Validators block: `R-5` (no run-on-injury), `R-7` (no two HARD-quality
sessions back-to-back), `R-11` (long-run cap as % of weekly run volume).

### 5. Mid-block events affect plan

End of June 2026 he tweaks his Achilles on a hill repeat. Adds to
`injury-log.md`:

```
Active:
- ## Right Achilles, low-grade insertional — 2026-06-28
  Tender to AM step. Pain 2/10 running, 0/10 cycling/swim.
  Action: drop run for 7 days, resume with walk/jog, no hills 3 weeks.
```

Friday's `/morning-check-in` reads the active flag and the upcoming
Saturday session is auto-modified: long run replaced with long bike + 15'
walk-jog instead of run-off-the-bike. Validator R-5 blocks any hill-run
session within 21 days; R-15 (build-recovery balance) gates pushing
volume back up.

### 6. Mid-block knowledge gap

He asks the agent: "What does the literature say about return to running
after low-grade insertional Achilles tendinopathy in a triathlete?"

```
coach research-gap "insertional achilles tendinopathy return to run protocol triathlete" --topic injury
```

Output (illustrative):
```
Local corpus: thin coverage (1 hit, score 0.42, mean rank 3.0)
Reason: thin_coverage

Suggested queries (paste into a browser; pick URLs to /ingest-research):
  1. peer_reviewed       BJSM             site:bjsm.bmj.com insertional achilles tendinopathy return to run protocol triathlete
  2. peer_reviewed       PubMed           site:pubmed.ncbi.nlm.nih.gov insertional achilles tendinopathy return to run protocol triathlete
  3. peer_reviewed       Google Scholar   site:scholar.google.com insertional achilles tendinopathy return to run protocol triathlete
  4. expert_practitioner Steve Magness    site:stevemagness.com insertional achilles tendinopathy return to run protocol triathlete
  5. expert_practitioner Joe Friel        site:joefrielsblog.com insertional achilles tendinopathy return to run protocol triathlete
```

He runs the BJSM search in a browser, finds two papers, paste-drops the
URLs into `/ingest-research`. The corpus now has peer-reviewed guidance
the next time the agent drafts an Achilles return progression.

> **GAP — closing the web loop.** This explicit-paste workflow is a chore.
> A WebSearch MCP wired with the same site-filter constraint, gated by
> "approve these 3 URLs?" would turn 5 minutes into 30 seconds.

### 7. B-race week — not a taper

Week 18, Lake George 70.3 (B). The plan-training-week skill recognises B-race
priority and applies a *micro-taper*: −20% volume Mon-Thu, opener Friday,
race Saturday, recover Sunday. CTL dips 3, recovers within 5 days.

> **GAP — race priority semantics aren't formalised.** A/B/C is metadata;
> the agent infers the taper aggressiveness via prompts, not validators.
> A typed `RacePriority` with phase-template overrides would make this
> deterministic.

### 8. Winter — indoor + cross-training

Lake Placid prep through New England winter means months of trainer rides
and treadmill runs. Diego enables `preferences.md` constraints:
"weekday rides indoor only Nov-Mar; long ride outdoor when temp > 28°F."

> **GAP — environmental conditions as planning input.** Today preferences
> are prose; the validator can't read "outdoor only > 28°F". A weather-
> aware scheduler that swaps the *type* of session (outdoor → indoor) but
> preserves its *intent* (Z2 endurance) would help cold-climate athletes.

### 9. Race-specific data

Diego wants to study Lake Placid bike segments. He uses the strava MCP:

```
"Show me the climb profile for the IMLP bike loop and any KOM efforts"
```

Strava pulls KOM segments. He'd like the agent to translate "I'll climb
Whiteface Memorial Highway 4x at IM watts" into specific session
constraints, but that course→session translation isn't built.

> **GAP — course → session translator.** Given a course profile (or a
> Strava route), produce climb-specific brick sessions, descent practice
> volumes, and aid-station fueling cues. Today this is a freeform
> conversation, not a tool.

### 10. Race week → race day

`/draft-race-plan` 4 weeks out renders a fully-fledged countdown. After
the race, `/review-week` on race week + a freeform "lessons learned"
journal entry feeds the next plan.

## What works today

- Long runways (>24 weeks) compose deterministically via the phase library.
- Multisport TSS distribution + adherence is computed correctly across swim/bike/run.
- Active-injury flags hard-block the relevant sport.
- B-race priority is honoured via prompt rules (informally).
- Knowledge gap detection points at peer-reviewed sources.

## Gaps surfaced

1. **Multi-A-race seasons** — composer doesn't produce two-peak chains.
2. **Course profile awareness** — climbing/descent/heat profile of an
   A-race doesn't shape session content.
3. **Race priority as a typed concept** — A/B/C only affects the chain via
   freeform prompts; should be a validator + chain-modifier.
4. **Closing the web research loop** — `/ingest-research` is paste-driven;
   a WebSearch MCP with explicit approval gate is the obvious next move.
5. **Environment/weather-aware scheduling** — preferences for "outdoor
   when X" can't be enforced; sessions don't auto-swap on forecast.
6. **Course → session translator** — Strava route + power profile should
   produce specific brick + climb-repeat sessions.
7. **Cross-training accounting** — when Diego does ski touring or Nordic
   skiing in January, that load isn't in his sport-specific TSS but does
   affect recovery. No "general endurance load" bucket today.
