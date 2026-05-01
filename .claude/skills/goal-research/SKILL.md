---
name: goal-research
description: When Sean declares a NEW goal/sub-goal with no plan structure yet (e.g. "build stronger legs for cycling, 8 weeks, 2 lifts/week" or "cut 3kg over 6 weeks"), research it against the trusted-source registry, ingest the notes, and emit a time-boxed plan-fragment YAML the composer can splice into weekly planning.
trigger: /goal-research <goal description> [--weeks <int>] [--cadence <e.g. 2x/week>] [--kind training|nutrition]
---

# Skill: goal-research

This skill produces TWO outputs from one declared goal:

1. **Research notes** under `knowledge/research/YYYY/MM/` (reuses
   `/ingest-research`).
2. **A plan-fragment YAML** under `plans/<plan-id>/fragments/<goal-slug>.yaml`
   — a structured, time-boxed sub-program the composer (`compose_chain`)
   loads as `chain.active_fragments` and `plan-training-week` splices into
   the weekly schedule.

This is NOT `/research-gap-fetch`. That skill closes a CORPUS gap when an
existing question lands without a good answer. This one fires when Sean
declares a brand-new goal that has no plan structure yet — strength block,
diet, focus area. The fragment is the new structure.

---

## Distinction at a glance

| Skill | Trigger | Output |
| --- | --- | --- |
| `/research-gap-fetch` | Existing question, thin corpus answer | Knowledge notes + decision log |
| `/research-gap-fetch` discovery branch | Existing question, no registered source | Knowledge notes + `sources-pending.yaml` draft + decision log |
| `/goal-research` (this skill) | NEW goal/sub-program, no plan structure | Knowledge notes + plan-fragment YAML + decision log |

If Sean is asking "what does the literature say about X?" use `/research-gap-fetch`. If he's saying "I want to do X for the next N weeks" use this skill.

---

## Inputs

- `goal description` — free-text declaration ("build stronger legs for
  cycling", "lower-back resilience routine", "race-week carb-load
  protocol").
- `--weeks N` — fragment lifetime (default 8). Hard floor 4, hard ceiling
  16. Persistent fragments are an anti-pattern — sub-programs sunset and
  Sean re-runs `/goal-research` if he wants more.
- `--cadence` — agent-parsed cadence string. The fragment YAML normalises
  to `cadence_per_week: <int>` per session.
- `--kind training|nutrition` — disambiguator when the goal isn't obvious.
  Defaults: lifting / mobility / sport-specific drills → `training`;
  diet / fueling / hydration → `nutrition`.

---

## Step 1 — Frame the gap

Treat the goal as a knowledge query and call `tempo.gap_search` to get
credibility-aware suggestions:

```python
from tempo.gap_search import detect_gap, suggest_research_queries
gap = detect_gap(goal_description, topic="<best-fit topic>")
# If gap is hits+confidence (corpus already covers it), still proceed —
# the goal might be 'do' rather than 'know'. Use the existing knowledge
# notes as research_refs in the fragment.
suggestions = suggest_research_queries(gap, k=3)
```

If `suggestions == []`, follow the discovery-branch pattern from
`/research-gap-fetch` Step 2D: ONE unconstrained WebSearch with the goal
phrased as a research query, classify each domain via
`classify_domain`, surface tentative tags via AskUserQuestion. Hard rules
inherited from `/research-gap-fetch`:

- Never auto-promote a discovered domain to `sources.yaml`. Append to
  `knowledge/sources-pending.yaml` only on explicit user approval.
- Tentative credibility stays `unvetted` unless the user explicitly
  upgrades.
- Cancel writes nothing.

## Step 2 — Run constrained WebSearch + approval (if registered sources covered the topic)

For each suggestion, call WebSearch with `suggestion.query` verbatim. Cap
at 5 results per query. Surface to AskUserQuestion exactly as
`/research-gap-fetch` does in Step 3 — credibility tag + URL + title.

Cancel = stop. Don't write the fragment, don't ingest, don't log.

## Step 3 — Ingest each approved URL

Call `/ingest-research <url>` for each approved URL. Stamp frontmatter
extras (mirroring `/research-gap-fetch` Step 4) but use
`ingest_via: goal-research` so future `search_memory` queries can
distinguish goal-research origin from gap-fetch origin.

Collect the resulting note paths — they become the fragment's
`research_refs[]`.

## Step 4 — Compose the plan-fragment YAML

Decide the fragment kind (`training` vs `nutrition`) and write
`plans/<plan-id>/fragments/<goal-slug>.yaml`. If `<plan-id>` doesn't
exist yet, surface that to the user — fragments need a plan to attach to.
For a Sean-only-has-rolling-plan case, attach to the rolling-base plan;
do not create a new plan.

### Training fragment shape

```yaml
fragment_id: stronger-legs-cycling-2026-04
goal: "build stronger legs for cycling, 2 lifts/week"
kind: training
created_at: 2026-04-30
re_evaluate_after: 2026-06-25         # created_at + duration_weeks * 7
duration_weeks: 8
sessions:
  - archetype: strength_intensification_block   # MUST exist in session-library/
    cadence_per_week: 2
    slot_preference: [tuesday, friday]
    target_tss: 22
    notes: "Run ≥24h before any hard run; cap at 60 min"
  - archetype: strength_realization_block
    cadence_per_week: 0    # 0 means "available but not weekly"; agent picks weeks
    target_tss: 12
research_refs:
  - knowledge/research/2026/04/strength-for-cycling-foss.md
  - knowledge/research/2026/04/ronnestad-heavy-strength-cycling.md
rationale: |
  Hypertrophy block already complete in March. Move to intensification
  per Rønnestad: 4-6 reps @ 80-90% 1RM, 4 weeks, then 2 weeks
  realization for race-prep window.
```

### Nutrition fragment shape

```yaml
fragment_id: race-week-carb-load-2027-04
goal: "race-week carb-load protocol for 70.3"
kind: nutrition
created_at: 2027-04-19
re_evaluate_after: 2027-05-02
duration_weeks: 2
nutrition_windows:
  - label: "carb-load days T-3..T-1"
    schedule: "2027-04-28..2027-04-30"
    macros: { carb_g_per_kg_per_day: 10, protein_g_per_kg_per_day: 1.6, fat_g_per_kg_per_day: 0.8 }
    notes: "Spread across 5 meals; low-fiber dinner T-1"
  - label: "race morning"
    schedule: "2027-05-01"
    macros: { carb_g: 120, protein_g: 20, fat_g: 5 }
    notes: "3h pre-start; oatmeal + banana + honey; tested combo per athlete-tested.yaml"
research_refs:
  - knowledge/research/2027/04/jeukendrup-race-week-fueling.md
rationale: |
  Race-week protocol per Jeukendrup; tested combos pulled from
  athlete-tested.yaml so we don't introduce novel fueling in race week
  (R-17, R-18).
```

### Schema rules — enforced by `tempo.fragments.load_fragment`

- A fragment is EITHER `training` (with `sessions[]`) OR `nutrition`
  (with `nutrition_windows[]`). Never both. Splitting a goal that wants
  both shapes (e.g. "strength + paired protein protocol") into two
  fragments keeps the lifecycle and the R-20 budget honest.
- `re_evaluate_after > created_at`. The duration is bounded — if Sean
  asks for 24 weeks, push back: "fragments are designed to sunset; pick
  ≤16 weeks and we'll re-evaluate."
- Every training-fragment `archetype` must exist in
  `knowledge/methodology/session-library/`. The loader fails closed.
  If you need a new archetype, add it to the session library FIRST
  (separate ticket / commit), then reference it.
- `target_tss` is per-session. The composer multiplies by
  `cadence_per_week` to compute the weekly contribution for R-20.

## Step 5 — Validate the fragment

```bash
uv run python -c 'from tempo.fragments import load_fragment; load_fragment("plans/<plan-id>/fragments/<slug>.yaml")'
```

This re-parses + validates against the schema (including archetype
existence). If it raises `FragmentSchemaError`, fix and retry — don't
commit a fragment that doesn't load.

## Step 6 — Log the decision

Call `coach-db.log_decision`:

- `scope`: `goal-research:<fragment_id>`
- `kind`: `goal_fragment_added`
- `rationale`: one paragraph that names the goal, the duration, the
  fragment kind, the research notes ingested, and the fragment YAML path.
  Include "active until <re_evaluate_after>" so future searches surface
  the lifecycle.
- `changed_files`: list every research note + the fragment YAML.

## Step 7 — Report

Tell Sean, terse:

- Goal as recorded.
- Fragment kind + duration + re-evaluation date.
- Research notes ingested (paths).
- Fragment YAML path.
- Heads-up: "this fragment will appear in next week's draft per R-20 budget."

If you cancelled at any approval gate, just say so — no fragment, no
notes, no decision logged.

---

## Hard rules — do not bend

- **Two outputs, always.** Research notes AND a fragment YAML. A run
  that produces only one is incomplete; either say what you couldn't do
  and stop, or finish.
- **Time-boxed only.** No fragment without `re_evaluate_after`. Persistent
  sub-programs are an anti-pattern.
- **Macro plan stays the anchor.** Fragments don't mutate `plan.yaml`.
  They live in `plans/<plan-id>/fragments/` and get loaded by
  `compose_chain` as `active_fragments`.
- **R-20 is SOFT, not HARD.** When a fragment + phase key_sessions exceed
  the weekly TSS budget by >15%, surface the violation and let Sean
  decide (drop a fragment session this week, accept the overage in
  changelog, or sunset the fragment early).
- **Never auto-promote sources.** The `/research-gap-fetch` discovery
  rules apply: pending entries go to `knowledge/sources-pending.yaml`,
  promotion to `sources.yaml` is a deliberate human act.
- **Never edit `gap_search.py` / `research.py` from this skill.** This
  skill is a CALLER. Refactors belong in their own tickets.

## Open design notes (for future fragment writers)

- **Fragment lifecycle**: 4-12 weeks default with explicit re-evaluation.
  A 16-week fragment is the ceiling — beyond that the goal probably
  belongs in `goals.yaml` and a plan re-bootstrap.
- **Reconciliation when fragment exceeds capacity**: SOFT R-20 with a
  warning + suggested swap-out. NOT a hard reject. Sean retains decision
  authority.
- **Same skill, different fragment shape (training vs nutrition)**:
  the schema enforces one-or-the-other so the R-20 budget calculation
  stays well-defined (nutrition fragments contribute 0 TSS).
- **No CLI verb yet.** Invocation is `/goal-research` only. A thin
  `coach goal-research compose <fragment-path>` verb may land later for
  testing — the user-facing surface stays the slash command.
