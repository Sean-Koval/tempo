---
type: methodology
topic: friel_periodization
description: Friel's triathlon periodization model — prep/base/build/peak/race/transition. Stub for ingestion.
sources: [friel-triathletes-training-bible, friel-blog]
---

# Friel periodization — working notes

## Phase sequence

1. **Preparation** (optional) — 2–4 wk. Return-to-training after a break.
2. **Base** — 12 wk (typical full IM). Aerobic capacity, force, technique. Three 4-week sub-phases (Base 1/2/3) with progressive volume.
3. **Build** — 6–8 wk. Race-specific intensity and muscular endurance. Two 3–4 week sub-phases.
4. **Peak** — 2 wk. Short, sharp, race-specific.
5. **Race** — taper week(s).
6. **Transition** — recovery post-race.

## Week pattern

3 build weeks → 1 recovery week. Recovery weeks cut volume ~40–50%, keep some intensity exposure.

## Abilities framework

Friel teaches training to develop *abilities* in sequence:
- Aerobic endurance
- Force (low-cadence strength)
- Muscular endurance (sustained sub-threshold)
- Anaerobic endurance
- Power
- Sprint power

Lower-numbered abilities are foundational; race-specific abilities emerge in build/peak.

## How Tempo applies it

- `phases.yaml` uses a simplified 4-phase split (base/build/peak/taper). The `anatomical_adaptation` phase in the 24-week IM template corresponds to Friel's "preparation".
- `decision-rules.md` R-15 codifies the 3:1 build-to-recovery cadence.
- `session-library.md` names are abilities-aware — e.g., `cruise_interval_run` is muscular endurance, `threshold_run` is anaerobic endurance.

## Open questions / to research

- How does Friel adjust for time-crunched IM athletes (< 12 h/wk)? Cross-ref with Dixon (purplepatch).
- Friel's position on polarized vs pyramidal distribution — his model is closer to pyramidal in Base, closer to polarized in Build.

## To expand

_Stub. Populate via `ingest-research` when working from the book directly._
