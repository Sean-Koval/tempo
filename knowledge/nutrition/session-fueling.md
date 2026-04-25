---
type: nutrition
topic: [in_session_fueling, carb_oxidation, gut_training]
phases: [base, build, peak, taper]
credibility: peer_reviewed
sources:
  - jeukendrup-mysportscience
  - burke-clinical-sports-nutrition
  - trainingpeaks-blog
key_claims:
  - Single-transportable carbohydrate intake plateaus around 60 g/hr because intestinal SGLT1 saturates; combining glucose:fructose at roughly 2:1 recruits GLUT5 and pushes oxidation to 90 g/hr or higher.
  - Sessions under 75 minutes don't require fuel for trained athletes — water and a fed pre-workout state cover them.
  - Above 2 hours, gut-tested intake of 60–90 g/hr preserves pace late in the session and shortens recovery; the ceiling is gut-tolerance, not energy demand.
  - Run fueling is harder than bike fueling at the same intensity — gut blood flow drops with foot-strike vibration, so target the lower end of the bike range and lean on liquids.
  - Athlete-tested totals from `athlete-tested.yaml` outrank these defaults whenever they conflict.
---

# Session fueling

How much to take in during training, by duration and sport. The literature
ceilings are well-established; the practical limit is what the gut tolerates
on race-relevant terrain at race-relevant intensity. The numbers below are
defaults — `knowledge/nutrition/athlete-tested.yaml` is the override.

## Defaults by duration

| Session length | Carbs (g/hr) | Notes |
| --- | --- | --- |
| < 75 min | 0 (water) | Fed state covers it. Don't train the gut to expect fuel here — wastes the adaptation. |
| 75 min – 2 hr | 30–60 | Single-source carb is fine (glucose or maltodextrin). |
| 2–4 hr | 60–90 | Multi-transportable required above ~60 g/hr (glucose:fructose ≈ 2:1). |
| 4 hr+ | 80–120 | Race-sim territory. Mix solids and liquids. Sodium 500–1000 mg/hr in heat. |

## Defaults by sport (at >2 hr durations)

- **Bike** — easiest gut conditions. 80–100 g/hr is realistic for trained
  athletes who've gut-trained. Solids (bars, waffles, real food) work well
  in the first half; lean on gels and fluid carbs as the session lengthens.
- **Run** — gut blood flow drops with vibration. Drop the bike target by
  ~20%: 60–80 g/hr practical ceiling for most. Liquids and gels dominate;
  solids late in a long run usually backfire.
- **Swim** — open-water sessions 90 min+ can take a gel at the pool edge or
  a sip of fluid carb at a feeding stop. Pool sets rarely warrant in-session
  fuel; fix it on the deck before the next set.

## Composition

- **Carb sources** — glucose-only above 60 g/hr exceeds SGLT1 transport and
  pools in the gut. Above 60 g/hr, mix glucose:fructose (or
  maltodextrin:fructose) at roughly 2:1. Most commercial sports drinks and
  modern gels (Maurten, SiS Beta Fuel, Precision PF&H) are formulated this
  way already.
- **Sodium** — 300–800 mg/hr baseline; 800–1500 mg/hr in heat or for heavy
  sweaters. Confirm with a sweat test, not feel.
- **Fluid** — match sweat rate, not thirst. 500–800 ml/hr typical; up to
  1000+ in heat.
- **Caffeine** — 1–3 mg/kg in the back half of long sessions for a
  perceived-effort lift. Treat it like a tool, not a default; tolerance
  builds quickly.

## How to layer carb sources

For a 3-hour ride targeting 80 g/hr:

- **Bottle 1 (concentrated):** 90 g carbs in 500 ml from a 2:1 mix drink → drink over hour 1.
- **Bottle 2 (water):** sip with the gel.
- **Hour 2:** 1 gel (~25 g) + 1 small solid (~30 g) + water.
- **Hour 3:** 2 gels (~50 g) + water.

Adjust upward only after the gut handles this volume cleanly across three
sessions. Log every long session to `athlete-tested.yaml` — that file is the
authority for what Sean's gut has actually tolerated.

## Common mistakes

- Eating *less* than the prescribed amount because "I don't feel like I need
  it" — the deficit shows up 90 minutes later as effort drift.
- Switching products on race day. The trained gut tolerates the trained
  product; novelty is a GI-distress accelerant.
- Single-source glucose above 60 g/hr → bloating, slosh, eventual GI shutdown.

## Caveats

- Numbers are for a ~70 kg adult endurance athlete. Scale loosely with body
  mass; cite athlete-tested.yaml for Sean's actual numbers.
- Heat raises both fluid and carb-tolerance demands. Cold weather
  *underestimates* both — most athletes underfuel in cool conditions.
- Gut training is itself a multi-week adaptation — see `gut-training.md`.
