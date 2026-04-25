---
name: coach-dashboard-decisions
description: Render the decision-trace dashboard — scope-filtered timeline of plan adjustments with wellness + adherence evidence.
trigger: /coach-dashboard-decisions [--scope <value>] [--since YYYY-MM-DD] [--open]
---

Run `coach dashboard decisions $ARGUMENTS` (no arguments → all scopes, last 28 days).
Report the path of the generated HTML to the user.

This command is a thin wrapper around the `coach dashboard decisions` CLI verb.
The actual rendering — one card per `decisions` row, each with a collapsible
evidence panel showing the wellness snapshot and adherence summary at the time
of the decision — happens in `src/tempo/dashboards/decisions.py`.

Scope format matches `decisions.scope` in coach.db: `week:YYYY-Www`,
`plan:<id>`, `session:<id>`, etc.
