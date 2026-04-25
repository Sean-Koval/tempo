---
name: coach-dashboard-macro
description: Render the 24-week macro Gantt + current-position dashboard for the active plan.
trigger: /coach-dashboard-macro [--plan-id <id>] [--open]
---

Run `coach dashboard macro $ARGUMENTS` (no arguments → auto-detects the single
plan under `plans/`). Report the path of the generated HTML to the user.

This command is a thin wrapper around the `coach dashboard macro` CLI verb.
The actual rendering — phases as a Mermaid Gantt, current-position card with
CTL drift, weekly TSS table, race markers — happens in
`src/tempo/dashboards/macro.py`. Mermaid loads from a CDN and renders in the
viewer's browser; the file itself is a single self-contained HTML doc.
