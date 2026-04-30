---
type: methodology
topic: session_library_index
description: Index for the per-sport session library files.
---

# Session Library

Canonical session names used in `plan.yaml` and `sessions_planned.library_ref`. Each entry has a stable `id` (snake_case), target duration/TSS range, purpose, and execution structure.

The `bootstrap-plan` and `plan-training-week` Skills compose from this library. `coach-db.find_similar_session` runs against `sessions.lance` (embedded from these files) before the agent is allowed to invent a new session.

Files in this directory, one per sport bucket:

- [`swim.md`](./swim.md) — pool / open-water sessions
- [`bike.md`](./bike.md) — road/trainer/gravel sessions
- [`run.md`](./run.md) — easy / long / quality run sessions
- [`brick.md`](./brick.md) — bike→run combined and race-rehearsal sessions
- [`strength.md`](./strength.md) — endurance-supporting strength templates

## Conventions

- HR zones by athlete's current LTHR (run) and power zones by FTP (bike).
- Durations and TSS are target ranges — `plan-training-week` picks the point value based on phase, adherence, and wellness.
- Any session not listed here needs either a new library entry (preferred) or explicit changelog justification for a one-off.
- Each file uses `## <Sport>` as the sport header and `### \`<id>\`` as the session heading. The embedder (`tempo.embed._iter_session_entries`) parses these headings to populate `sessions.lance` with sport tags.
