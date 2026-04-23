---
type: methodology
topic: decision_rules
description: Heuristics for when to back off, when to push, when to stop. Always validated against before a weekly plan is finalized.
---

# Decision Rules

These fire during `plan-training-week` and `review-week`. The agent validates every drafted session against them; any flagged session gets either backed off or an explicit override rationale in the changelog.

Severity:
- **HARD** — never override, even with rationale.
- **SOFT** — can override with a documented reason.
- **WATCH** — informational; flag in review.

---

## Wellness

### R-1 HRV downward trend + negative TSB (SOFT)
If HRV is trending down 3+ consecutive days **and** TSB < -20:
→ cut the next hard session by 30–50% or convert to Z2.

### R-2 Low readiness (SOFT)
If `readiness < 5/10` for 2+ consecutive days:
→ swap today's planned intensity for Z2 volume at same sport.

### R-3 Sleep deficit (WATCH)
If 3-day avg sleep < 6.5h:
→ flag in review; if a race-pace session is scheduled in next 48h, move it.

### R-4 Illness-adjacent symptoms (HARD)
If sore throat / elevated RHR > +8bpm / reported "coming down with something":
→ no intensity until symptoms clear for 48h.

---

## Injury

### R-5 Active injury flag (HARD)
If `injury-log.md` has any active flag:
→ respect all listed constraints. Do not schedule anything in the forbidden categories. Substitute with equivalent TSS from a non-affected sport when possible.

### R-6 Pain during warmup (HARD)
If user reports pain during warmup that doesn't clear within 10 minutes:
→ abandon the session and log to `injury-log.md`.

### R-7 Bone stress red flags (HARD)
If localized sharp pain in tibia/metatarsal/femur that persists past the session:
→ no running, no plyos, no >Z3 on bike until cleared. Log immediately.

---

## Load & ramp

### R-8 CTL ramp rate cap (SOFT)
Weekly CTL ramp > +8 (1-day TSS avg):
→ cut next week's volume by 15% unless explicit macro reason.

### R-9 Sustained negative TSB (SOFT)
TSB < -25 for 5+ days:
→ schedule a recovery week next if not already planned.

### R-10 TSB recovery before A-race (HARD)
In peak + taper phases, do not schedule a session that would push TSB below -15.

---

## Session placement

### R-11 No back-to-back hard (SOFT)
Do not schedule the hardest bike and hardest run on consecutive days.

### R-12 Long day anchors (SOFT)
Long ride and long run should be separated by ≥1 easy or rest day. Exception: brick (`brick_run_off_bike`, `race_sim_brick`) where they're fused by design.

### R-13 Swim-first on hard-bike days (WATCH)
If a hard bike is scheduled, put the swim before it (morning), not after — stroke quality collapses with fatigue.

---

## Progression

### R-14 Long-run progression cap (HARD)
Weekly long-run duration increase > +15% week-over-week:
→ reduce. Exception: first week after a down week.

### R-15 Down-week cadence (SOFT)
3 build weeks → 1 down week. Skipping a down week for > 2 consecutive cycles triggers a WATCH flag.

### R-16 Race-pace introduction timing (SOFT)
No race-pace sessions until the build phase. Exception: tune-up race day.

---

## Fueling

### R-17 Long-session fueling rehearsal (HARD)
Every long ride > 3h and long run > 90 min must have a documented fueling plan matched to race-day intent. Novel fueling products do not appear for the first time in race week.

### R-18 Gut training gate (WATCH)
Race-day fueling must have been tolerated in ≥3 race-sim sessions before race week per `knowledge/nutrition/athlete-tested.yaml`.

---

## Override protocol

Any SOFT rule override requires:
1. A line in `plans/<plan-id>/changelog.md` explaining the reasoning.
2. A `log_decision` entry via `coach-db` with `kind=adjust`, `rationale=<why>`.
3. A WATCH flag added to the next week's review brief.
