---
name: coach-dashboard-week
description: Render the single-week HTML dashboard (planned vs actual + wellness + load + changelog) for the most recent completed week or one passed via $ARGUMENTS.
trigger: /coach-dashboard-week [YYYY-Www] [--plan-id <id>] [--open]
---

Run `coach dashboard week $ARGUMENTS` (no arguments → defaults to last completed week).
Report the path of the generated HTML to the user. If `--open` was passed, the CLI
will also launch the file in the default browser.

This command is a thin wrapper around the `coach dashboard week` CLI verb. The actual
rendering — including session deltas, wellness sparklines, load delta, and changelog
extracts — happens in `src/tempo/dashboards/week.py`. There is no LLM reasoning here.
