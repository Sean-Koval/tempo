# 2027 Half Ironman — plan rationale

**Built:** 2026-04-25
**Runway:** 53 weeks
**Template:** `ironman_half_16wk` (last 16 weeks) + a 37-week injury-driven pre-block

## 1. Goal framing

A-race: Half Ironman (70.3), placeholder date **2027-05-01**, venue TBD.
Sean has confirmed the rough timing ("a year out") but the exact event is open.
The plan is anchored to that placeholder; if it shifts ±2 weeks the only thing
that needs to move is `goal.yaml.date` + the `start_week` on the final taper —
re-running `/bootstrap-plan` handles both.

## 2. Constraints — the BSI dominates

`athlete/injury-log.md` flags an active grade-2 left-tibia bone stress injury as
of 2026-04-25. Per `decision-rules.md` R-5 (HARD) and R-7 (HARD), this overrides
all other planning concerns:

- **No running** for at least 6 weeks (target reassessment 2026-06-06).
- **No high-impact loading** — no plyos, no jumping, careful with rough-terrain
  out-of-saddle bike efforts.
- **Swim deferred** until PT clearance (also targeted 2026-06-06 per the
  injury-log entry).

Effect on the plan: the first ~10 weeks are bike-and-strength led, with swim
re-entering on PT clearance and run re-entering as supervised walk/jog.

## 3. Framework chosen

The standard Tempo template for a 70.3 (`ironman_half_16wk`) is 16 weeks long.
Sean has 53. SKILL.md Step 4 says "if `weeks_until_target` is longer than the
template, either extend the base phase or add a maintenance block before the
template starts."

I chose to **add a 37-week pre-block** rather than stretch the template.
Rationale: stretching `ironman_half_16wk` 3× would dilute the build/peak phases
which are the parts that actually shift fitness toward race specificity. A
maintenance-shaped pre-block better matches the actual training need — get
healthy, rebuild aerobic base across all three sports, then run the standard
template at full intensity in the final 16 weeks.

The pre-block is structured in five phases that mirror Friel's progression
(prep → base 1 → base 2 → bridge → race build):

| Phase | Weeks | Character |
| --- | --- | --- |
| `rehab_bike_only` | 6 | bike + strength only; injury recovery |
| `return_to_3sport` | 4 | phased swim + walk/jog re-entry |
| `aerobic_base_1_volume` | 9 | volume rebuild across 3 sports, Z2 dominant |
| `aerobic_base_2_durability` | 9 | durability + first tempo-bike block |
| `pre_build_bridge` | 9 | bridge — first race-pace touches, brick exposure |
| `base` (template) | 6 | standard 70.3 base |
| `build` (template) | 6 | race-specific intensity + pacing |
| `peak` (template) | 2 | sharpening + race sim |
| `taper` (template) | 2 | freshen |

Knowledge cited:
- `knowledge/methodology/ironman-build-structure.md` — Tempo's synthesis of
  Friel/CTS/Couzens/Dixon principles. (`expert_practitioner` credibility.)
- `knowledge/methodology/friel-periodization.md` — phase sequence and 3:1
  build:recovery cadence (R-15). (`expert_practitioner`.)
- `knowledge/nutrition/gut-training.md` — gut training is a base-and-build
  ability that cannot start in race week (informs R-17/R-18 in build/peak).

## 4. Key adaptations to the template

- **No anatomical-adaptation phase as such** — `rehab_bike_only` already serves
  the function (low-load bike Z2, anatomical strength) but is enforced by the
  injury, not chosen for periodization aesthetics.
- **Sport-rebalance gradient** — bike is 85% of TSS in week 1 and tapers to
  40% by the time the standard template kicks in. Swim and run grow in.
- **Run-volume cap in `return_to_3sport`** — 15-min/session, 2×/wk, walk/jog.
  This is more conservative than typical return-to-run protocols; if the PT
  clears a more aggressive ramp at 2026-06-06, override via changelog.
- **Knowledge gap flagged**: the corpus does not have a specific
  return-to-run-from-BSI protocol. `coach-db.search_knowledge` returned zero
  bone-stress-specific results — only general session library and gut training.
  `/ingest-research` should be run on a peer-reviewed BSI return-to-run paper
  (e.g., Warden et al., *Br J Sports Med* 2014, or AMSSM stress fracture
  guidelines) before phase 2 starts.

## 5. Assumptions (these are what could break the plan)

- **PT clearance at ~2026-06-06.** If the BSI hasn't healed by then, push the
  whole pre-block back and tighten the bridge accordingly. The standard 16-week
  build is the load-bearing piece; the pre-block is compressible.
- **Weekly hours budget = 8 h/wk** — placeholder. `preferences.md` is empty.
  TSS targets are scaled from a 10-hr template assumption; once Sean fills in
  hours, all `weekly_tss_target` ranges should be recalibrated.
- **No fall B-race.** If Sean adds one (e.g., a Sept marathon-bike fondo, an
  Olympic-distance tri), the maintenance block can absorb a 3-week peak/taper
  — re-bootstrap when declared.
- **FTP/CSS/threshold-pace unknown.** Profile is empty. First weeks will have
  to use perceived-effort + HR-based intensity targets until a field test
  (Phase 1 GAP — Sean will need to test once he's a few weeks into bike base).
- **Steady-state CTL trajectory not yet computable.** `coach.db` has no load
  history; `coach sync` hasn't run. Re-evaluate `weekly_tss_target` in
  `aerobic_base_1_volume` once 4 weeks of synced data exist.

## 6. Open questions for Sean

1. **Race date + venue.** Is 2027-05-01 right? Maine 70.3, Eagleman, Texas 70.3,
   Boulder? Confirm so the taper math is real.
2. **Weekly hours.** Realistic budget? 8 h/wk during rehab is conservative;
   could be 10–12 in deep base.
3. **Strength access.** Gym/home setup? `strength_foundation` assumes
   barbell + standard accessories.
4. **Indoor vs outdoor bike split.** Affects how I write workout duration vs
   power targets in plan-training-week.
5. **Any prior BSI history?** Pattern (same tibia, opposite leg, metatarsal)
   would change the run-return aggressiveness.
