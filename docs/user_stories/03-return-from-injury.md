# 03 — Return from injury into a half-Ironman

## Persona

**Sean** (the dogfood case), 38. Diagnosed with **tibial bone stress
injury (BSI)** on 2026-04-12, MRI-confirmed grade 2. Has signed up for a
**half-Ironman in May 2027** — about 53 weeks out from today. Cleared
by ortho to bike + swim only, run review at 8 weeks (2026-06-07), then
PT-graded return-to-run protocol.

This story matches the real `plans/2027-half-ironman/` plan in this repo;
the gaps are observed from real usage.

## Goal

Build a plan that respects the injury today, restores running progressively,
and arrives at the half-IM ready to race. Avoid the common failure mode of
blowing the BSI on week 9 by ramping run volume too fast.

## Step-by-step

### 1. Declare the constraint *before* declaring the plan

Sean updates `athlete/injury-log.md`:

```
## Active

- ## Tibial BSI, right — diagnosed 2026-04-12
  Grade 2 MRI, ortho clearance: bike + swim only, no run.
  Review 2026-06-07 (8 weeks). Then PT-graded return-to-run.
  HARD constraint on plan: zero run sessions until cleared.
```

### 2. Bootstrap with active injury

```
/bootstrap-plan
```

Composer pipeline (now in `src/tempo/composition.py`):

1. Reads `injury-log.md`, sees BSI Active.
2. `injury_types_from_flags` returns `{"bsi"}`.
3. `derive_injury_preconditions("bsi")` →
   `{"active_injury_no_run", "pt_clearance"}`.
4. Picks `triathlon_half_16wk` template for the terminal block (May 2027).
5. Prepends a rehab preblock: `rehab_bike_only` (8wk) → `return_to_3sport`
   (4wk) → `aerobic_base_1_volume` (extends to fill runway).
6. Drops the leading `prep_*` phase from the template since rehab serves
   that purpose.

`rationale.md` documents the BSI-driven structure. `changelog.md` entry:
"plan created with BSI active — rehab preblock prepended."

### 3. Validation — agent should not silently accept this

Sean re-reads the plan output. **[GAP encountered in earlier session]**:
the system *did* compose the plan correctly, but adjacent components had
gaps:

- `search_knowledge "BSI return to run protocol"` returned **zero hits**.
- The agent still drafted week 1 of `return_to_3sport` using "general
  orthodoxy" without flagging the knowledge vacuum.

Today (after US-4) the flow is:
```
coach research-gap "tibial BSI return to run protocol grade 2" --topic injury
```
which prints:
```
Local corpus: 0 hits — KnowledgeGap (no_hits)

Suggested queries:
  1. peer_reviewed   BJSM    site:bjsm.bmj.com tibial BSI return to run protocol grade 2
  2. peer_reviewed   PubMed  site:pubmed.ncbi.nlm.nih.gov tibial BSI return to run …
  3. peer_reviewed   Scholar site:scholar.google.com tibial BSI return to run …
  4. expert_practitioner Steve Magness site:stevemagness.com …
  …
```

He runs the BJSM and PubMed queries, finds 3 protocols, ingests them.
Knowledge corpus now has peer-reviewed guidance.

> **GAP — preflight should *call* research-gap, not require manual run.**
> Plan-training-week's preflight should detect "phase=return_to_3sport
> and corpus has 0 BSI hits" and surface the knowledge gap as a
> calibration debt entry. Today the user has to know to run it.

### 4. Weeks 1-8 — bike + swim only

Sessions for week 1:
- Mon — swim 2500m aerobic
- Tue — bike trainer 75min Z2
- Wed — swim 3000m threshold
- Thu — bike trainer 90min sweet-spot
- Fri — strength (no plyometric, no impact)
- Sat — long bike outdoor 2:30 Z2
- Sun — swim 2000m + mobility

**Validators block running**: any agent suggestion for a run session in
this window returns `R-5` HARD violation, refusing to write the week file.

### 5. Week 8 — clearance review

PT clears him to start walk/jog protocol. Sean updates:

```
## Active
(none)

## Cleared (recent)
- ## Tibial BSI — cleared 2026-06-07 (with conditions)
  Conditions: walk/jog protocol weeks 1-3, max 5×(2'jog+2'walk).
  Re-check 4 weeks. No hills, no track until week 6 of return.
```

The phase advances to `return_to_3sport` automatically (auto-resolver in
`bootstrap-plan` recomputes phase position from date + chain).

### 6. Return-to-run sessions need a fundamentally different shape

The session library has `easy_run`, `tempo_run`, `interval_run`, `long_run`
— but `walk_jog_progression`, `single_leg_loaded_run`, `run_volume_first_no_quality`
weren't there originally. Sean drafted them in the last session and the
plan-training-week skill emitted them as one-off compositions.

> **[GAP — US-10]**: session library auto-promotion. When a skill
> invents a session that's not in the library, after first use it should
> propose adding it. Today the same return-to-run session is drafted
> from scratch each week, drifting in volume and progression rules.

### 7. Mid-return setback

Week 11 (week 3 of return-to-run): Tuesday's 4×(3'jog+2'walk) leaves the
shin sensitive Wed AM. Soreness 2/10, no pain on hop test.

Sean updates `injury-log.md`:
```
## Watching
- ## Tibial BSI re-flare risk — 2026-06-30
  Soreness post-Tue session. NOT cleared yet, just watching.
  Skip Thu run, swap to bike. Re-evaluate Sun.
```

`/morning-check-in` reads this, the Thu draft auto-substitutes a bike
ride for the run, and `log_decision` records the swap.

> **GAP — "Watching" tier isn't a typed validator concept.** Active is
> HARD; Watching should be a SOFT validator that requires rationale
> on any session in the affected sport. Today it's prose the agent reads.

### 8. Phase transition to base 1

Week 13 — clearance is full. Plan transitions to `aerobic_base_1_volume`.
Run volume can grow more freely, but with progression caps (R-13: weekly
run volume +10% max).

Macro dashboard shows:
```
Phase            Weeks    Status
rehab_bike_only  1-8      ✓ completed
return_to_3sport 9-12     ✓ completed
aerobic_base_1   13-22    ▶ current
…
peak             47-49
taper            50-52
race week        53       2027-05-02
```

### 9. Plan amendment — race date confirmed

Initial plan used a placeholder race date. The actual 2027-half-IM is
**2027-05-08**, not 2027-05-02. Sean wants:

- Shift target +6 days.
- Re-derive phase boundaries.
- Append a changelog entry.

Today this is a careful agent prompt that edits `goal.yaml`,
`plan.yaml`, and `changelog.md`. **[GAP — US-8]**: a `coach plan amend`
CLI doesn't exist. He'd want:

```
coach plan amend --shift-target +6d --reason "Race date confirmed at registration"
```

…to do all three atomically.

### 10. End-state

Sean races the half-IM at 5:38, healthy through the entire build, with a
full audit trail in `plans/2027-half-ironman/changelog.md` showing every
sub-plan adjustment and the data behind each one.

## What works today

- Composer takes injury flags as input and produces a chain that includes
  rehab preblocks (`rehab_bike_only` → `return_to_3sport`).
- HARD validators (R-5) cannot be overridden — agent literally cannot
  draft a run during BSI active.
- `coach research-gap` flags zero-hit knowledge gaps with credible
  source-scoped queries.
- Decision rules + plan changelog + decisions table form an auditable
  trail.

## Gaps surfaced

1. **Preflight should auto-detect knowledge vacuums.** Plan-training-week
   should call `detect_gap` for the active phase's domain (e.g., "BSI return
   to run") and surface the gap as calibration debt — not wait for the user
   to run `coach research-gap`.
2. **"Watching" injury tier as a SOFT validator.** Today only Active /
   Cleared are mechanically meaningful.
3. **Session library auto-promotion (US-10).** Return-to-run sessions
   drift each week because the agent re-invents them.
4. **`coach plan amend` (US-8).** Race date shifts, phase reorderings,
   priority changes are still freeform agent edits.
5. **Calibration debt for protocol-driven phases.** When a phase has a
   protocol (PT-graded return), the plan should reflect that protocol
   field-by-field — week-by-week run minutes, walk:jog ratios — not be
   hand-derived each week.
6. **Re-injury detection.** Wellness drops + soreness journal entries
   should trigger a "you may be re-flaring; recommend backing off X" alert,
   not require Sean to recognise the pattern.
7. **PT/clinician collaboration export.** Sean's PT wants a one-pager
   showing "what we did this week vs. the protocol." There's no export.
