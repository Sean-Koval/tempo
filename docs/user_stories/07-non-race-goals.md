# 07 — Non-race goals: FTP target, masters swim meet, gran fondo, strength block

## Persona

Three short personas in one file, because the gaps overlap.

- **Lena**, 32, cyclist. Wants to **raise her FTP from 248W → 280W in
  16 weeks**. No race; pure performance goal.
- **Tomás**, 52, masters swimmer. **State masters meet 2026-09-12** — 8
  weeks out. Specialist 100/200 free.
- **Aiyana**, 27, gravel cyclist. **Gran fondo 2026-08-22**, 130km, 2200m
  climbing — first long event.
- **Wes**, 39, hybrid athlete. Wants to **add 25kg to his squat 1RM and
  hold his half-marathon time** over a 14-week strength-emphasis block.

The thread: Tempo today is built around endurance race events. Non-race
performance goals and strength-emphasis blocks are second-class.

## Step-by-step

### Lena — FTP target

```yaml
# athlete/goals.yaml
- id: ftp-280-2026
  type: performance_target
  metric: ftp_w
  current: 248
  target: 280
  by_date: 2026-08-16  # 16 weeks
  notes: "No race. Pure FTP push."
```

She runs `/bootstrap-plan`. There's no A-race; the composer needs to
handle "performance target" as an alternative anchor.

> **[GAP — composer requires an A-race anchor].** Today the chain is
> built around `race-calendar.yaml`, not `goals.yaml`. `compose_chain`
> needs a "non-race goal" mode that picks templates differently.

A reasonable chain for Lena would be:
```
weeks 1-4   aerobic_base_1_volume        Z2 mileage, restore base
weeks 5-10  ftp_progression_block_a      sweet-spot 4×8 → 3×15 → 2×20
weeks 11-13 vo2max_polarisation_block    HIT 5×3 / 4×4 / 3×5
weeks 14-15 ftp_test_taper               freshen
week  16    ftp_test                     20-min test → derive new FTP
```

None of those phases (except `aerobic_base_1_volume`) exist in the phase
library today.

> **GAP — performance-target phase library.** Need
> `ftp_progression_block`, `vo2_polarisation_block`, `ftp_test_taper`,
> `css_progression_block` (for Tomás), `1rm_peak_block` (for Wes).

### Tomás — masters swim meet

```yaml
- id: masters-meet-2026
  date: 2026-09-12
  type: masters_swim_meet
  events: ["100_free", "200_free"]
  priority: A
```

The composer has `masters_swim_meet_8wk` template (added in US-7). Good.
But the session library is currently dominated by triathlon-style swim
sessions: long aerobic, threshold sets, technique. Sprint specialists need:

- Quality-over-volume blocks.
- Speed-endurance sets (8×50 race-pace + 30 rest).
- Lactate tolerance work.
- Race-specific taper (sharper than triathlon swim taper).

> **GAP — sport-specific session libraries.** Today the session library
> is a single markdown file mixing all sports. A specialist swim block
> doesn't have the right archetypes.

> **GAP — multi-event meet handling.** Tomás races 100 *and* 200 free.
> Their training emphases differ. There's no concept of "event-specific
> peak" inside a meet.

### Aiyana — gran fondo

The composer has `gran_fondo_12wk` template. Aiyana fills profile,
declares the event, runs `/bootstrap-plan`. Output looks reasonable.

But — gran fondos are mass-start social events. Pacing strategy isn't
"hold X watts for Y hours" the way a TT is; it's "draft the right group,
go hard up the climbs, recover on flats."

`/draft-race-plan` produces a generic endurance-race countdown. Doesn't
talk about group dynamics, drafting strategy, or descent technique.

> **GAP — event-archetype-specific race plans.** "Gran fondo" needs a
> different race-day brief from "marathon" needs a different brief from
> "Ironman." Today `/draft-race-plan` has one shape.

She also wants nutrition guidance for a 5-6 hour event with on-course
aid every 25km. Current nutrition corpus is Ironman-biased
(70-90g/hr, structured). Gran fondo is more variable (real food at
aid stations, cafe stops).

> **GAP — event-specific nutrition profiles.** `nutrition/athlete-tested.yaml`
> is keyed on Sean's IM use case. Per-event nutrition templates would
> help.

### Wes — hybrid (strength + endurance maintenance)

```yaml
- id: squat-1rm-2026
  type: performance_target
  metric: squat_1rm_kg
  current: 145
  target: 170
  by_date: 2026-09-30
- id: hm-time-maintain
  type: performance_maintain
  metric: half_marathon_time
  current: "1:34:00"
  by_date: 2026-09-30
```

Wes's goals conflict — meaningful 1RM gains while running 25-30 km/wk is
hard. He needs:

- Strength block periodisation (linear/conjugate/block).
- Concurrent training conflicts: leg day 24-48h before quality run is bad.
- Explicit interference-effect awareness in the validator.

Today the strength corner of the methodology is a single phase
(`winter_base_strength`) with placeholder sessions. There's no concept
of "strength as the primary goal."

> **GAP — strength as a first-class block.** Strength is currently a
> support layer for endurance plans. Hybrid athletes need strength as
> the lead, with endurance maintained.

> **GAP — concurrent training interference rules.** R-7 prevents two
> HARD-quality endurance sessions back-to-back. There's no analogous
> rule for "heavy leg session before quality run" — which is the
> killer pattern for hybrid athletes.

## What works today

- Composition library *does* include several non-IM/marathon templates:
  `gran_fondo_12wk`, `masters_swim_meet_8wk`, `road_race_10wk`.
- Performance targets *can* be declared in `goals.yaml`.
- Strength is acknowledged in the phase library (`winter_base_strength`).

## Gaps surfaced

1. **Composer non-race-goal mode.** `compose_chain` should accept a
   `goals.yaml` performance target as an anchor, not just a race date.
2. **Performance-target phase library.** `ftp_progression_block`,
   `vo2_polarisation_block`, `css_progression_block`, `1rm_peak_block` —
   none exist.
3. **Sport-specific session libraries.** Swim/bike/run/strength session
   archetypes need separation; sprint-specialist swim sets are missing,
   gravel/fondo bike sets are missing.
4. **Multi-event meet handling.** Single A-race → multiple events
   within (100 + 200 free, sprint distance + Olympic).
5. **Event-archetype race plans.** `/draft-race-plan` should branch on
   event type — IM, marathon, half-IM, 5K, fondo, swim meet — each
   needs a different countdown shape.
6. **Event-specific nutrition profiles.** Beyond Ironman.
7. **Strength as a first-class block.** Hybrid athletes need strength-led
   plans with endurance maintenance.
8. **Concurrent training interference validator rules.** "Heavy leg
   session ≤24h before quality run" should be SOFT/HARD per athlete
   preference.
9. **Goal types beyond targets and races.** "Daily ride streak", "audax
   200/300/400/600", "rim-to-rim hike", "summit attempt" — these don't
   fit into either `race-calendar.yaml` or the current `goals.yaml`
   schema cleanly.
