---
type: methodology
topic: session_library
description: Named session archetypes. Always prefer a library ref over inventing new vocabulary.
---

# Session Library

Canonical session names used in `plan.yaml` and `sessions_planned.library_ref`. Each entry has a stable `id` (snake_case), target duration/TSS range, purpose, and execution structure.

The `bootstrap-plan` and `plan-training-week` Skills compose from this library. `coach-db.find_similar_session` runs against `sessions.lance` (embedded from this file) before the agent is allowed to invent a new session.

---

## Swim

### `technique_swim`
- **Purpose:** stroke quality, not fitness
- **Duration:** 45–60 min
- **TSS:** ~30–45
- **Structure:** 400 WU → 4×50 drill focus (catch-up, fingertip-drag, sculling) → 8–12×50 @ stroke-count-target → 400 CD

### `aerobic_swim_set`
- **Purpose:** Z2 swim volume
- **Duration:** 45–75 min
- **TSS:** ~45–65
- **Structure:** 400 WU → 3–5×600 @ CSS+10" with 30" rest → 200 CD

### `race_pace_swim`
- **Purpose:** open-water race-pace rehearsal
- **Duration:** 60–75 min
- **TSS:** ~55–75
- **Structure:** 400 WU → 6–10×200 @ race effort with sighting → 200 CD

---

## Bike

### `easy_aerobic_ride`
- **Purpose:** recovery or volume with no stimulus
- **Duration:** 45–90 min
- **TSS:** 35–60
- **Structure:** steady Z1–low Z2, high cadence, no surges

### `long_ride_z2`
- **Purpose:** aerobic capacity, durability, fat oxidation
- **Duration:** 2.5–5 hours (phase-dependent)
- **TSS:** 120–300
- **Structure:** steady Z2 by HR drift; last 20% may drift to Z2-top if feeling good; fuel 60–90g carbs/hr

### `tempo_bike_block`
- **Purpose:** aerobic power, sub-threshold muscular endurance
- **Duration:** 90–120 min
- **TSS:** 90–140
- **Structure:** 15 WU → 2–4×20min @ 76–88%FTP with 5 easy → CD

### `race_pace_bike`
- **Purpose:** IM-specific pacing practice
- **Duration:** 3–5 hours
- **TSS:** 180–300
- **Structure:** 30 WU → 90–180min @ race NP (65–72% FTP for IM, 78–85% for 70.3) with fueling protocol → CD

### `longer_long_ride`
- **Purpose:** peak-phase overload; goes longer than race duration for the bike split
- **Duration:** 5–6 hours
- **TSS:** 250–350
- **Structure:** steady Z2 with 2×30min @ race pace in the back half

### `threshold_bike`
- **Purpose:** raise FTP
- **Duration:** 75–90 min
- **TSS:** 80–110
- **Structure:** 15 WU → 3–4×10min @ 95–102%FTP with 5 easy → CD

---

## Run

### `easy_aerobic_run`
- **Purpose:** volume without stimulus
- **Duration:** 30–60 min
- **TSS:** 30–60
- **Structure:** steady Z1–low Z2 by HR

### `long_run_z2`
- **Purpose:** aerobic capacity, durability, running economy under fatigue
- **Duration:** 75–150 min (phase-dependent)
- **TSS:** 70–150
- **Structure:** Z2 by HR (drift-watched); last 15 min may drift to Z2-top; fuel 40–60g/hr past 75 min

### `cruise_interval_run`
- **Purpose:** threshold development at sustainable effort
- **Duration:** 60–75 min
- **TSS:** 65–90
- **Structure:** 15 WU → 3–5×8–12min @ Z4 (HR) with 2 easy → 10 CD

### `threshold_run`
- **Purpose:** lactate threshold
- **Duration:** 60 min
- **TSS:** 75–95
- **Structure:** 15 WU → 2×15–20min @ LTHR with 4 easy → CD

### `tempo_run`
- **Purpose:** aerobic power, sub-threshold
- **Duration:** 45–70 min
- **TSS:** 55–80
- **Structure:** 15 WU → 20–35min @ Z3 → CD

### `hill_strides`
- **Purpose:** neuromuscular, running economy
- **Duration:** 45 min
- **TSS:** 45
- **Structure:** aerobic run with 6–10×20" hill strides late

### `race_week_primer_run`
- **Purpose:** maintain neuromuscular activation in taper
- **Duration:** 30–40 min
- **TSS:** 35–45
- **Structure:** 15 easy → 4–6×30" @ race pace → 10 easy

---

## Brick & combined

### `brick_run_off_bike`
- **Purpose:** running off the bike at target HR
- **Duration:** bike + 20–45 min run
- **TSS:** 100–180 total
- **Structure:** scheduled bike (usually race_pace_bike, shortened) → transition < 5 min → run steady at target IM run HR

### `race_sim_brick`
- **Purpose:** peak-phase race rehearsal
- **Duration:** bike 3–4h + run 45–75 min
- **TSS:** 220–320 total
- **Structure:** full bike at race NP with race fueling → race-pace run for prescribed duration; full kit/nutrition rehearsal

### `race_day_fueling_rehearsal`
- **Purpose:** validate gut + pacing plan end-to-end
- **Duration:** full race duration minus swim
- **TSS:** 300+
- **Structure:** as race_sim_brick but with strict adherence to the documented race-day protocol; log gut response to `athlete-tested.yaml`

---

## Strength

### `strength_foundation`
- **Purpose:** anatomical adaptation, injury prevention
- **Duration:** 45 min
- **TSS:** 20 (est.)
- **Structure:** 3×8–10 squat, deadlift, split squat, row, core

### `strength_maintenance`
- **Purpose:** preserve strength during build/peak
- **Duration:** 30 min
- **TSS:** 15 (est.)
- **Structure:** 2×5 heavy (compound), core, done

---

## Conventions

- HR zones by athlete's current LTHR (run) and power zones by FTP (bike).
- Durations and TSS are target ranges — `plan-training-week` picks the point value based on phase, adherence, and wellness.
- Any session not listed here needs either a new library entry (preferred) or explicit changelog justification for a one-off.
