---
type: nutrition
topic: [gut_training, in_session_fueling, gi_distress]
phases: [base, build, peak]
credibility: peer_reviewed
sources:
  - jeukendrup-mysportscience
  - fitzgerald-80-20
  - featherstone-feedthemachine
key_claims:
  - The gut is a trainable organ — intestinal carbohydrate transporter density (SGLT1, GLUT5) up-regulates with consistent in-session carb exposure across 4–8 weeks.
  - Untrained athletes typically tolerate 30–45 g/hr; well-trained guts handle 90–120 g/hr without GI distress.
  - Progress in 10–15 g/hr increments week-over-week, holding each step for at least 2 sessions before increasing — chasing a number too fast is the leading cause of in-session GI failure.
  - Race-day intake should never exceed the highest intake successfully tolerated in 3+ race-sim sessions at race-pace intensity (rule R-18).
  - GI distress on a long session is data, not a setback — drop intake 20% the next session, hold for 3 sessions, then resume progression.
---

# Gut training

The gut adapts to what you train it to do. A bigger carbohydrate-tolerance
ceiling means a bigger fuel pipeline on race day, which means more sustained
power and pace late in the event. The adaptation takes weeks, not days —
race-week is the wrong time to attempt a new carb intake.

`knowledge/nutrition/athlete-tested.yaml` is the ground-truth log of what
Sean's gut has actually tolerated. The progressions below are scaffolding;
the YAML log is the authority for race planning decisions.

## The progression

| Week | Target g/hr | Session length | Notes |
| --- | --- | --- | --- |
| 1 | 45 | 90 min ride | Single-source carb fine. Establish baseline tolerance. |
| 2 | 60 | 2 hr ride | Switch to 2:1 glucose:fructose. |
| 3 | 70 | 2.5 hr ride | Add a brick run-off-bike at 30 min. |
| 4 | 80 | 3 hr ride | Race-product trial begins. |
| 5 | 80 (hold) | 3 hr ride + 30 min run | Hold; assess GI response. |
| 6 | 90 | 3.5 hr ride | First race-pace block (20–30 min) inside the long session. |
| 7 | 90 (hold) | Race-sim brick (4 hr ride + 30 min run) | Validate at race-pace. |
| 8 | 100 (only if 90 felt clean) | Full race-sim | Final ceiling check. |

Each row is a *target*, not a quota. If a session goes wrong, drop back one
row and hold for two sessions before re-attempting.

## Signs of GI distress

Listed in order of escalation:

1. **Slosh / fullness** — fluid pooling in stomach. Usually a sign that gut
   blood flow is overwhelmed; back off intensity for 5–10 min and let it clear.
2. **Burping / reflux** — gas production from undigested carbs. Stop solid
   intake; switch to dilute fluid carb only.
3. **Cramping** — early sign of motility shutdown. Walk aid stations, switch
   to water + small sodium dose.
4. **Nausea** — gastric emptying has stopped. Stop all intake for 15–30 min,
   then restart with sips of cool water.
5. **Vomiting / urgent bathroom** — full GI shutdown. Session is over;
   recovery is days, not hours.

If any of these surface above level 1 in a session, that session's intake
exceeded current gut tolerance. Drop the next session's target by 20% and
hold for 3 sessions before resuming progression.

## Recovering from a bad session

- **Day 0 (the bad session):** rehydrate with a low-carb electrolyte drink.
  No solid food for 2 hours, then bland carbs (rice, banana).
- **Day 1:** normal eating, normal hydration. Gentle Z1 only if at all.
- **Day 2–3:** resume normal training. Next fueling session: drop 20% from
  the failed target; use a known-tolerated product (not a new one).
- **Log it** to `athlete-tested.yaml` with `gut_response: 1–2` and notes on
  what changed (heat, intensity, new product, timing).

## What gut-training is NOT

- Not a way to compensate for inadequate base fitness — gut tolerance does
  not generate horsepower; it just lets you sustain it longer.
- Not a substitute for race-day rehearsal at race-pace intensity. Easy
  rides at 80 g/hr don't prove anything about race-pace at 80 g/hr —
  intensity is the variable that breaks the gut.
- Not a one-time accomplishment. Tolerance regresses with disuse over
  weeks. Maintain at least one >90 g/hr session every 10–14 days during
  build and peak phases.

## How this applies to a full Ironman race plan

- Required threshold: 3+ race-sim sessions completed at the proposed
  race-day g/hr target (R-18, HARD). The agent will refuse to draft a
  race-day fueling plan above this threshold without a documented override.
- The athlete-tested ceiling, not the literature ceiling, is the upper
  bound on race-day intake. If `athlete-tested.yaml` shows max tolerated
  carbs/hr at 75 g, race day is 75 g — even though literature supports 100+.

## Caveats

- Heat reduces gut blood flow and shifts the tolerance ceiling down 10–20 g/hr.
  Heat-acclimated athletes recover most of the gap.
- Caffeine briefly increases gastric motility — useful in the back half of
  long sessions, can be counterproductive if loaded early.
- Some athletes have a hard genetic ceiling on fructose absorption
  (fructose malabsorption). If 2:1 mixes consistently produce GI distress
  while glucose-only is tolerated, that's the signal — switch to
  maltodextrin + small fructose ratio (4:1 or 5:1).
