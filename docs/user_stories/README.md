# Tempo — User Stories

These are scenario-driven walk-throughs from the perspective of athletes
using Tempo to construct, run, and adapt training plans. Each story names a
**persona**, walks through the **step-by-step interaction** they'd have with
the agent and the CLI, and closes with a **Gaps surfaced** section calling
out features that are missing or rough from the user's point of view.

The intent is product discovery, not specification. The stories are deliberately
concrete — full of plausible commands, plausible numbers, plausible setbacks —
so the gaps surface naturally rather than as abstract checklists.

## Index

| # | Story | Persona | Primary use case |
|---|-------|---------|------------------|
| 01 | [First marathon — 18-week build](01-first-marathon.md) | Single-sport runner, BQ-curious | Goal declaration → phased build → race day |
| 02 | [Ironman build, long runway](02-ironman-build.md) | Triathlete, 30+ wk to A-race | Multisport macro plan, weekly cadence |
| 03 | [Return from injury into a half-Ironman](03-return-from-injury.md) | Athlete with active BSI | Rehab → return-to-sport → race-ready |
| 04 | [Weekly snapshot — Sunday rhythm](04-weekly-snapshot.md) | Athlete mid-block | Review last week, draft next, push to calendar |
| 05 | [Mid-cycle progress check + adaptation](05-progress-and-adaptation.md) | Any athlete, week 8 of 16 | "Am I on track?" + load-trajectory correction |
| 06 | [Pivot to a new goal mid-plan](06-pivot-to-new-goal.md) | Athlete whose A-race date changes | Plan amendment, decision logging |
| 07 | [Non-race goals — FTP target, masters swim meet, gran fondo](07-non-race-goals.md) | Cyclist/swimmer/strength-curious | Goal types beyond running/triathlon |

## How to read these

- `coach …` is the local Typer CLI (in `src/tempo/cli.py`).
- `/skill-name` is a Claude Code slash command backed by a Skill in
  `.claude/skills/`.
- Bracketed text like **[GAP]** marks behaviour that doesn't yet exist.
- Quoted dialogue is illustrative — what the user would *expect* to see, not
  a literal transcript.

## Cross-cutting gaps

Themes that show up in more than one story:

1. **Onboarding is a steep cliff.** Every story starts with the user already
   having `athlete/profile.yaml`, `race-calendar.yaml`, `injury-log.md`,
   intervals.icu auth, and 4+ weeks of synced load. There's no `coach init`
   or guided wizard; cold start is a documentation read.
2. **The "what changed and why" trail is split** across `plans/<id>/changelog.md`,
   `data/coach.db.decisions`, and `journal/`. The decisions dashboard helps,
   but there's no single "show me the story of this plan" view.
3. **Plan amendments are still freeform agent edits**, not atomic CLI ops.
   `coach plan amend` (US-8) is filed but not built — date shifts, phase
   re-ordering, and goal swaps all need careful prompting today.
4. **Web research is suggested but not fetched.** `coach research-gap`
   prints queries; the user has to alt-tab to a browser and feed URLs back
   via `/ingest-research`. Closing that loop (with an approval gate) is the
   biggest UX lift left.
5. **Non-Ironman/marathon shapes are second-class.** Composition library
   covers marathons, halves, fondos, swim meets, and sprint→full triathlons,
   but ultras, stage races, multi-A-race seasons, and pure strength blocks
   aren't first-class.
6. **Mobile / on-the-go capture is missing.** Everything assumes a terminal.
   Morning check-in, mid-run setbacks, and "I felt great today" notes need
   a phone-friendly entry path that ends up in `journal/` + `coach.db`.
